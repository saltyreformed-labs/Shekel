"""
Shekel Budget App -- Reference Cache and Status Boolean Column Tests

Tests for the ref_cache module (Commit #1) and the boolean columns added
to the Status model.  Verifies that:

  - The cache loads all StatusEnum and TxnTypeEnum members at startup.
  - The cache raises RuntimeError when a database row is missing.
  - The Status boolean columns (is_settled, is_immutable, excludes_from_balance)
    are correct for every status.
  - settled_contribution(Transaction) respects the boolean columns.
  - The grid shows "Paid" instead of "Done" for the mark-done button.
  - GoalMode and IncomeUnit ref_cache accessors return valid IDs.
  - GoalModeEnum and IncomeUnitEnum match their database rows exactly.
  - The two-axis recurrence vocabulary (RecurrenceUnit / PeriodPlacement /
    BusinessDayShift) resolves and matches its rows exactly (step R2a).
  - The amount model's AmountSource vocabulary resolves and matches its rows
    exactly, and has no ``own`` row (plan step X-au-c1, ruling R-FI).
"""

from decimal import Decimal

import pytest
import sqlalchemy.exc

from app.extensions import db
from app import create_app, ref_cache
from app.ref_cache import _accessors, _state
from app.enums import (
    AcctCategoryEnum,
    AmountSourceEnum,
    BusinessDayShiftEnum,
    FilingStatusEnum,
    GoalModeEnum,
    IncomeUnitEnum,
    LedgerAccountClassEnum,
    LedgerAccountKindEnum,
    LoanAnchorSourceEnum,
    PeriodPlacementEnum,
    PostingKindEnum,
    PostingSourceEnum,
    RaiseTypeEnum,
    RecurrenceUnitEnum,
    StatusEnum,
    TxnTypeEnum,
)
from app.models.ref import (
    AmountSource,
    BusinessDayShift,
    FilingStatus,
    GoalMode,
    IncomeUnit,
    LedgerAccountClass,
    LedgerAccountKind,
    PeriodPlacement,
    PostingKind,
    PostingSource,
    RaiseType,
    RecurrenceUnit,
    Status,
    TransactionType,
)
from app.services.cash_ledger import contribution_of
from app.services.row_valuation import settled_contribution
from tests._test_helpers import (
    amount_basis_for,
    cover_bare_settled_row,
    one_off_row_of,
    settle_day_columns,
)


class TestRefCacheStatuses:
    """Tests for ref_cache status ID resolution."""

    def test_ref_cache_loads_all_statuses(self, app, db):
        """ref_cache.status_id() returns an integer for every StatusEnum member."""
        with app.app_context():
            for member in StatusEnum:
                result = ref_cache.status_id(member)
                assert isinstance(result, int), (
                    f"status_id({member.name}) returned {type(result)}, expected int"
                )

    def test_ref_cache_loads_all_txn_types(self, app, db):
        """ref_cache.txn_type_id() returns an integer for every TxnTypeEnum member."""
        with app.app_context():
            for member in TxnTypeEnum:
                result = ref_cache.txn_type_id(member)
                assert isinstance(result, int), (
                    f"txn_type_id({member.name}) returned {type(result)}, expected int"
                )

    def test_ref_cache_fails_on_missing_status(self, app, db):
        """ref_cache.init() raises RuntimeError when a status row is missing."""
        with app.app_context():
            # Delete one status row to trigger the failure.
            projected = (
                db.session.query(Status)
                .filter_by(name="Projected")
                .one()
            )
            db.session.delete(projected)
            db.session.flush()

            with pytest.raises(RuntimeError, match="Projected"):
                ref_cache.init(db.session)

            # Roll back so other tests aren't affected.
            db.session.rollback()

            # Re-init cache with all rows present.
            ref_cache.init(db.session)

    def test_init_records_unavailable_table_and_keeps_others_usable(
        self, app, db, monkeypatch
    ):
        """A missing ref table is reported, skipped, and never fatal.

        Simulates the pre-migration bootstrap window by making the
        ``loan_anchor_sources`` read meet a REAL missing-table refusal
        (SQLSTATE 42P01): since plan step balance:X-dj the tolerance reads
        that code, so a forged error without it would be -- correctly --
        re-raised (ruling R-BAL211).  ``init()`` must: record that one table
        in its returned ``unavailable`` list, still load every other table,
        and leave the unavailable table's accessor raising ``KeyError`` (an
        empty map) rather than returning a wrong value.  ``create_app`` relies
        on a non-empty return here to skip Jinja-globals registration
        (``app/__init__.py``).
        """
        with app.app_context():
            # Patched on the proxy's CLASS (finding BAL-569): monkeypatch on
            # the process-lived ``db.session`` INSTANCE restores by writing
            # the bound method onto it, shadowing any later class-level
            # patch (``test_commit_helpers._spy_rollback`` says how).
            real_query = type(db.session).query

            def fake_query(session, model):
                if model.__name__ == "LoanAnchorSource":
                    session.execute(
                        sqlalchemy.text("SELECT 1 FROM ref.no_such_table_x_dj"),
                    )
                return real_query(session, model)

            monkeypatch.setattr(type(db.session), "query", fake_query)
            unavailable = ref_cache.init(db.session)

            # Only the failed table is reported unavailable.
            assert unavailable == ["loan_anchor_sources"]
            # Every other table still loaded and resolves normally.
            assert isinstance(ref_cache.status_id(StatusEnum.PROJECTED), int)
            # The unavailable table's accessor raises KeyError, not a wrong ID.
            with pytest.raises(KeyError):
                ref_cache.loan_anchor_source_id(LoanAnchorSourceEnum.ORIGINATION)

            # Restore a fully-populated cache so later tests are unaffected.
            monkeypatch.undo()
            ref_cache.init(db.session)


class TestCreateAppRefCacheGate:
    """``create_app``'s ``init_ref_cache`` flag gates the eager ref_cache init.

    Locks the deploy-ordering fix: the migration host
    (``scripts/init_database.py``) builds the app with ``init_ref_cache=False``
    so the strict ref_cache row-check does NOT fire on a pre-migration
    database.  ``ref_cache.init`` is fatal on a missing row in an existing ref
    table (a genuine seed/data drift), and a migration that ADDS rows to an
    existing ref table -- like Build-Order Step 3's ``income`` / ``expense``
    posting kinds and ``transaction`` source -- leaves those rows absent until
    the migration the host is about to run actually applies them.  Eager-initing
    there raised and aborted the deploy (rolled back by ``shekel-deploy``);
    skipping it lets the host run the seeding migrations.  Gunicorn, the dev
    server, and the test app keep the eager default (True).
    """

    def test_init_ref_cache_false_skips_ref_cache_init(self, app, monkeypatch):
        """``create_app(init_ref_cache=False)`` never calls ``ref_cache.init``.

        The migration host must build the app without the eager row-check so it
        can run the very migrations that seed the missing rows.  A spy on
        ``ref_cache.init`` must record zero calls.
        """
        calls = []
        monkeypatch.setattr(
            ref_cache, "init", lambda session: calls.append(session) or [],
        )
        create_app("testing", init_ref_cache=False)
        assert calls == []

    def test_init_ref_cache_default_runs_ref_cache_init(self, app, monkeypatch):
        """``create_app()`` (the runtime default) eagerly calls ``ref_cache.init``.

        Gunicorn and the dev/test server rely on the eager init so the cache and
        the ref-id Jinja globals are ready before the first request.
        """
        calls = []
        monkeypatch.setattr(
            ref_cache, "init", lambda session: calls.append(session) or [],
        )
        create_app("testing")
        assert len(calls) == 1


class TestStatusBooleanColumns:
    """Tests for the boolean columns on the Status model."""

    def test_status_boolean_columns_correct(self, app, db):
        """All 5 statuses have the correct boolean column values.

        Expected:
          Projected:  settled=F, immutable=F, excludes=F
          Paid:       settled=T, immutable=T, excludes=F
          Received:   settled=T, immutable=T, excludes=F
          Credit:     settled=F, immutable=T, excludes=T
          Cancelled:  settled=F, immutable=T, excludes=T

        **The matrix is TOTAL over the table, and it was not before.**  This
        looked each expected name up by ``filter_by(name=...).one()`` and never
        asked what ELSE the table held, so a status the seed no longer names --
        exactly what plan step **balance:X-am** does to ``Settled`` -- would
        have left it green while the row survived in every database.  A test
        for a DELETION that cannot see the deleted thing measures nothing
        (``lessons.md``).  The set comparison below is what grades the
        migration; the per-row loop grades the flags.
        """
        with app.app_context():
            expected = {
                "Projected": (False, False, False),
                "Paid": (True, True, False),
                "Received": (True, True, False),
                "Credit": (False, True, True),
                "Cancelled": (False, True, True),
            }
            seeded = {row.name for row in db.session.query(Status).all()}
            assert seeded == set(expected), (
                "ref.statuses does not hold exactly the expected rows: "
                f"unexpected {sorted(seeded - set(expected))}, "
                f"missing {sorted(set(expected) - seeded)}"
            )
            assert {member.value for member in StatusEnum} == set(expected), (
                "StatusEnum and ref.statuses name different sets -- "
                "ref_cache.init raises for a member with no row, and a row "
                "with no member is dead vocabulary nothing can reach"
            )
            for name, (settled, immutable, excludes) in expected.items():
                status = (
                    db.session.query(Status).filter_by(name=name).one()
                )
                assert status.is_settled == settled, (
                    f"{name}: is_settled={status.is_settled}, expected {settled}"
                )
                assert status.is_immutable == immutable, (
                    f"{name}: is_immutable={status.is_immutable}, expected {immutable}"
                )
                assert status.excludes_from_balance == excludes, (
                    f"{name}: excludes_from_balance={status.excludes_from_balance}, "
                    f"expected {excludes}"
                )


class TestEffectiveAmount:
    """Tests for Transaction.effective_amount with boolean status columns."""

    def test_effective_amount_returns_zero_for_excluded_status(
        self, app, db, seed_user, seed_periods
    ):
        """effective_amount returns Decimal('0') for Credit status
        (excludes_from_balance=True).
        """
        with app.app_context():
            credit_id = ref_cache.status_id(StatusEnum.CREDIT)
            expense_type = (
                db.session.query(TransactionType)
                .filter_by(name="Expense").one()
            )

            txn = one_off_row_of(
                seed_periods[0],
                name="Credited Expense",
                amount=Decimal("250.00"),
                user_id=seed_periods[0].user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=expense_type.id,
                category_id=seed_user["categories"]["Groceries"].id,
            )
            txn.status_id = credit_id
            db.session.flush()

            assert settled_contribution(txn) == Decimal("0")

    def test_effective_amount_uses_actual_for_settled_status(
        self, app, db, seed_user, seed_periods
    ):
        """effective_amount returns actual_amount for Paid status
        (is_settled=True) when actual_amount is set.
        """
        with app.app_context():
            done_id = ref_cache.status_id(StatusEnum.DONE)
            expense_type = (
                db.session.query(TransactionType)
                .filter_by(name="Expense").one()
            )

            txn = one_off_row_of(
                seed_periods[0],
                name="Paid Expense",
                amount=Decimal("500.00"),
                user_id=seed_periods[0].user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=expense_type.id,
                category_id=seed_user["categories"]["Rent"].id,
            )
            txn.status_id = done_id
            # The settle day laid on BARE, as ``add_txn`` lays it (X-f1); the
            # record is the covering movement written after the flush (X-bi-4b-2).
            for _column, _value in settle_day_columns(seed_periods[0].start_date).items():
                setattr(txn, _column, _value)
            db.session.flush()
            # The record's home is the covering movement (X-bi-4b-1).
            cover_bare_settled_row(db.session, txn, "500.00", submitted="487.00")

            assert settled_contribution(txn) == Decimal("487.00")

    def test_effective_amount_uses_the_plan_figure_for_projected(
        self, app, db, seed_user, seed_periods
    ):
        """effective_amount returns the PLAN figure for Projected status.

        The row is a one-off since plan step balance:X-bi-7c, so its
        ``estimated_amount`` column is ``None`` and the figure is its
        definition's, resolved by amount rule 3 -- which is what the reader
        below asks and a raw column read would not.

        Asked of the AMOUNT MODEL since plan step X-bx: a Projected row has not
        settled, so what it contributes is
        :func:`~app.services.cash_ledger.contribution_of` and never the
        settled-only accessor its two siblings above use.  Same figure, same
        rule; the accessor simply no longer answers for this row (**BAL-465**).
        """
        with app.app_context():
            expense_type = (
                db.session.query(TransactionType)
                .filter_by(name="Expense").one()
            )

            txn = one_off_row_of(
                seed_periods[0],
                name="Projected Expense",
                amount=Decimal("500.00"),
                user_id=seed_periods[0].user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=expense_type.id,
                category_id=seed_user["categories"]["Rent"].id,
            )
            db.session.flush()

            assert contribution_of(txn, amount_basis_for(txn)) == Decimal("500.00")


class TestGridShowsPaidNotDone:
    """Tests that the grid UI shows 'Paid' instead of 'Done'."""

    def test_grid_shows_paid_not_done(self, app, auth_client, seed_user,
                                      seed_periods):
        """The full-edit form for an expense shows 'Paid' button, not 'Done'.

        Verifies the template rename from Commit #1.
        """
        with app.app_context():
            expense_type = (
                db.session.query(TransactionType)
                .filter_by(name="Expense").one()
            )

            txn = one_off_row_of(
                seed_periods[0],
                name="Test Expense",
                amount=Decimal("100.00"),
                user_id=seed_periods[0].user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=expense_type.id,
                category_id=seed_user["categories"]["Rent"].id,
            )
            db.session.commit()

            resp = auth_client.get(f"/transactions/{txn.id}/full-edit")
            assert resp.status_code == 200

            html = resp.data.decode()
            # The mark-done button should say "Paid", not "Done".
            assert "Paid" in html
            # "Done" should not appear as a button label (it may appear
            # in other contexts like status dropdown options).
            assert "> Done<" not in html


class TestGoalModeRefCache:
    """Tests for ref_cache goal mode ID resolution."""

    def test_goal_mode_ref_cache(self, app, db):
        """ref_cache.goal_mode_id() returns distinct positive integers
        for both GoalModeEnum members (FIXED and INCOME_RELATIVE).
        """
        with app.app_context():
            fixed_id = ref_cache.goal_mode_id(GoalModeEnum.FIXED)
            income_relative_id = ref_cache.goal_mode_id(GoalModeEnum.INCOME_RELATIVE)

            assert isinstance(fixed_id, int), (
                f"goal_mode_id(FIXED) returned {type(fixed_id)}, expected int"
            )
            assert isinstance(income_relative_id, int), (
                f"goal_mode_id(INCOME_RELATIVE) returned {type(income_relative_id)}, expected int"
            )
            assert fixed_id > 0, f"FIXED id should be positive, got {fixed_id}"
            assert income_relative_id > 0, (
                f"INCOME_RELATIVE id should be positive, got {income_relative_id}"
            )
            assert fixed_id != income_relative_id, (
                f"FIXED and INCOME_RELATIVE should have different IDs, both are {fixed_id}"
            )

    def test_goal_mode_enum_matches_db(self, app, db):
        """Every GoalModeEnum member has a corresponding database row,
        and every database row has a corresponding enum member.
        No extra rows, no missing rows.
        """
        with app.app_context():
            db_rows = db.session.query(GoalMode).all()
            db_names = {row.name for row in db_rows}
            enum_values = {member.value for member in GoalModeEnum}

            assert db_names == enum_values, (
                f"GoalMode DB rows {db_names} do not match "
                f"GoalModeEnum values {enum_values}"
            )
            assert len(db_rows) == len(GoalModeEnum), (
                f"GoalMode has {len(db_rows)} rows but GoalModeEnum has "
                f"{len(GoalModeEnum)} members"
            )


class TestIncomeUnitRefCache:
    """Tests for ref_cache income unit ID resolution."""

    def test_income_unit_ref_cache(self, app, db):
        """ref_cache.income_unit_id() returns distinct positive integers
        for both IncomeUnitEnum members (PAYCHECKS and MONTHS).
        """
        with app.app_context():
            paychecks_id = ref_cache.income_unit_id(IncomeUnitEnum.PAYCHECKS)
            months_id = ref_cache.income_unit_id(IncomeUnitEnum.MONTHS)

            assert isinstance(paychecks_id, int), (
                f"income_unit_id(PAYCHECKS) returned {type(paychecks_id)}, expected int"
            )
            assert isinstance(months_id, int), (
                f"income_unit_id(MONTHS) returned {type(months_id)}, expected int"
            )
            assert paychecks_id > 0, f"PAYCHECKS id should be positive, got {paychecks_id}"
            assert months_id > 0, f"MONTHS id should be positive, got {months_id}"
            assert paychecks_id != months_id, (
                f"PAYCHECKS and MONTHS should have different IDs, both are {paychecks_id}"
            )

    def test_income_unit_enum_matches_db(self, app, db):
        """Every IncomeUnitEnum member has a corresponding database row,
        and every database row has a corresponding enum member.
        No extra rows, no missing rows.
        """
        with app.app_context():
            db_rows = db.session.query(IncomeUnit).all()
            db_names = {row.name for row in db_rows}
            enum_values = {member.value for member in IncomeUnitEnum}

            assert db_names == enum_values, (
                f"IncomeUnit DB rows {db_names} do not match "
                f"IncomeUnitEnum values {enum_values}"
            )
            assert len(db_rows) == len(IncomeUnitEnum), (
                f"IncomeUnit has {len(db_rows)} rows but IncomeUnitEnum has "
                f"{len(IncomeUnitEnum)} members"
            )


class TestLedgerAccountClassRefCache:
    """Tests for ref_cache ledger-account-class resolution (posting ledger)."""

    def test_ledger_account_class_ref_cache(self, app, db):
        """ref_cache.ledger_account_class_id() returns a distinct positive int
        for every LedgerAccountClassEnum member.
        """
        with app.app_context():
            ids = {
                member: ref_cache.ledger_account_class_id(member)
                for member in LedgerAccountClassEnum
            }
            for member, class_id in ids.items():
                assert isinstance(class_id, int), (
                    f"ledger_account_class_id({member.name}) returned "
                    f"{type(class_id)}, expected int"
                )
                assert class_id > 0, (
                    f"{member.name} id should be positive, got {class_id}"
                )
            assert len(set(ids.values())) == len(LedgerAccountClassEnum), (
                f"LedgerAccountClassEnum members must have distinct IDs; "
                f"got {ids}"
            )

    def test_ledger_account_class_enum_matches_db(self, app, db):
        """Every LedgerAccountClassEnum member has exactly one DB row and
        every DB row has a member.  No extra rows, no missing rows.
        """
        with app.app_context():
            db_rows = db.session.query(LedgerAccountClass).all()
            db_names = {row.name for row in db_rows}
            enum_values = {member.value for member in LedgerAccountClassEnum}

            assert db_names == enum_values, (
                f"LedgerAccountClass DB rows {db_names} do not match "
                f"LedgerAccountClassEnum values {enum_values}"
            )
            assert len(db_rows) == len(LedgerAccountClassEnum), (
                f"LedgerAccountClass has {len(db_rows)} rows but "
                f"LedgerAccountClassEnum has {len(LedgerAccountClassEnum)} "
                f"members"
            )

    def test_is_debit_normal_correct_per_class(self, app, db):
        """ledger_class_is_debit_normal() returns the correct natural-balance
        side for every class, read through the cached meta map.

        Fundamental double-entry accounting: a debit increases an Asset or
        Expense balance (debit-normal -> True); a credit increases a
        Liability, Income (revenue), Equity, or Unrealized-gain balance
        (credit-normal -> False).  These values are the entire reason the
        ``LedgerAccountClass`` table exists, so they are pinned by hand -- and
        the map is asserted TOTAL over the enum, because a class seeded with
        the wrong side would present every one of its accounts inverted and
        nothing else in the suite would say so.
        """
        expected = {
            LedgerAccountClassEnum.ASSET: True,
            LedgerAccountClassEnum.LIABILITY: False,
            LedgerAccountClassEnum.INCOME: False,
            LedgerAccountClassEnum.EXPENSE: True,
            LedgerAccountClassEnum.EQUITY: False,
            # Other comprehensive income (ruling R-FO): a gain is a CREDIT.
            LedgerAccountClassEnum.UNREALIZED: False,
        }
        assert set(expected) == set(LedgerAccountClassEnum), (
            "every ledger account class must pin its natural-balance side "
            f"here; unpinned: {set(LedgerAccountClassEnum) - set(expected)}"
        )
        with app.app_context():
            for member, is_debit_normal in expected.items():
                class_id = ref_cache.ledger_account_class_id(member)
                result = ref_cache.ledger_class_is_debit_normal(class_id)
                assert result is is_debit_normal, (
                    f"{member.name}: ledger_class_is_debit_normal("
                    f"{class_id})={result}, expected {is_debit_normal}"
                )

    def test_ledger_class_is_debit_normal_raises_on_unknown_id(self, app, db):
        """ledger_class_is_debit_normal() raises on an unknown class_id.

        The accessor is logic-bearing (it decides whether a reader negates
        an account's posting sum), so a bogus class_id must fail loud
        rather than silently default to a wrong-but-valid False.
        """
        with app.app_context():
            known = {
                ref_cache.ledger_account_class_id(m)
                for m in LedgerAccountClassEnum
            }
            bogus_id = max(known) + 1000
            with pytest.raises(KeyError):
                ref_cache.ledger_class_is_debit_normal(bogus_id)

    def test_ref_cache_fails_on_missing_ledger_class(self, app, db):
        """ref_cache.init() raises RuntimeError when a ledger-class row is
        missing.

        Proves the enum<->seed parity gate fires for the new posting-ledger
        ref table exactly as it does for the long-standing Status table:
        deleting the 'Asset' row leaves LedgerAccountClassEnum.ASSET
        unresolvable, which must be a fatal startup error, not a silent
        skip.
        """
        with app.app_context():
            asset = (
                db.session.query(LedgerAccountClass)
                .filter_by(name="Asset")
                .one()
            )
            db.session.delete(asset)
            db.session.flush()

            with pytest.raises(RuntimeError, match="Asset"):
                ref_cache.init(db.session)

            # Roll back so other tests aren't affected, then re-init clean.
            db.session.rollback()
            ref_cache.init(db.session)


class TestPostingKindRefCache:
    """Tests for ref_cache posting-kind resolution."""

    def test_posting_kind_ref_cache(self, app, db):
        """ref_cache.posting_kind_id() returns a positive int for TRANSFER."""
        with app.app_context():
            transfer_id = ref_cache.posting_kind_id(PostingKindEnum.TRANSFER)
            assert isinstance(transfer_id, int), (
                f"posting_kind_id(TRANSFER) returned {type(transfer_id)}, "
                f"expected int"
            )
            assert transfer_id > 0, (
                f"TRANSFER id should be positive, got {transfer_id}"
            )

    def test_posting_kind_resolves_income_and_expense(self, app, db):
        """ref_cache resolves the Step-3 INCOME and EXPENSE kinds distinctly.

        The two cash-leg kinds added in Build-Order Step 3 must each
        resolve to a distinct positive ID, distinct from Step 2's TRANSFER.
        This guards that the enum ``.value`` strings match the seeded
        ``ref.posting_kinds.name`` rows and that no two members collapse
        onto one row (a copy-paste value collision).
        """
        with app.app_context():
            transfer_id = ref_cache.posting_kind_id(PostingKindEnum.TRANSFER)
            income_id = ref_cache.posting_kind_id(PostingKindEnum.INCOME)
            expense_id = ref_cache.posting_kind_id(PostingKindEnum.EXPENSE)
            assert income_id > 0 and expense_id > 0, (
                f"income/expense kind ids must be positive, got "
                f"income={income_id}, expense={expense_id}"
            )
            assert len({transfer_id, income_id, expense_id}) == 3, (
                f"posting kind ids collide: transfer={transfer_id}, "
                f"income={income_id}, expense={expense_id}"
            )

    def test_posting_kind_resolves_loan_correction_kinds(self, app, db):
        """ref_cache resolves the Step-4 loan-correction kinds distinctly.

        The four legs of a confirmed loan payment's real-split correction
        (``principal`` / ``interest`` / ``escrow`` / ``refund``) added in
        Build-Order Step 4 must each resolve to a distinct positive ID,
        distinct from one another and from the Step 2/3 kinds.  This guards
        that the enum ``.value`` strings match the seeded
        ``ref.posting_kinds.name`` rows and that no two members collapse onto
        one row (a copy-paste value collision).
        """
        with app.app_context():
            existing = {
                ref_cache.posting_kind_id(PostingKindEnum.TRANSFER),
                ref_cache.posting_kind_id(PostingKindEnum.INCOME),
                ref_cache.posting_kind_id(PostingKindEnum.EXPENSE),
            }
            loan_kinds = {
                member: ref_cache.posting_kind_id(member)
                for member in (
                    PostingKindEnum.PRINCIPAL,
                    PostingKindEnum.INTEREST,
                    PostingKindEnum.ESCROW,
                    PostingKindEnum.REFUND,
                )
            }
            for member, kind_id in loan_kinds.items():
                assert isinstance(kind_id, int) and kind_id > 0, (
                    f"{member.name} id must be a positive int, got {kind_id}"
                )
            # Four distinct loan kinds, none colliding with the earlier three.
            assert len(set(loan_kinds.values())) == 4, loan_kinds
            assert existing.isdisjoint(set(loan_kinds.values())), (
                f"loan kinds {loan_kinds} collide with earlier kinds {existing}"
            )

    def test_posting_kind_resolves_opening_and_trueup(self, app, db):
        """ref_cache resolves the loan read-switch OPENING and TRUEUP kinds.

        The two loan-genesis leg kinds added by the loan read switch (the
        deferred second half of Build-Order Step 4) must each resolve to a
        distinct positive ID, distinct from one another and from every Step
        2/3/4 kind.  This guards that the enum ``.value`` strings match the
        seeded ``ref.posting_kinds.name`` rows and that no two members
        collapse onto one row (a copy-paste value collision).
        """
        with app.app_context():
            existing = {
                ref_cache.posting_kind_id(member)
                for member in (
                    PostingKindEnum.TRANSFER,
                    PostingKindEnum.INCOME,
                    PostingKindEnum.EXPENSE,
                    PostingKindEnum.PRINCIPAL,
                    PostingKindEnum.INTEREST,
                    PostingKindEnum.ESCROW,
                    PostingKindEnum.REFUND,
                )
            }
            new_kinds = {
                member: ref_cache.posting_kind_id(member)
                for member in (
                    PostingKindEnum.OPENING,
                    PostingKindEnum.TRUEUP,
                )
            }
            for member, kind_id in new_kinds.items():
                assert isinstance(kind_id, int) and kind_id > 0, (
                    f"{member.name} id must be a positive int, got {kind_id}"
                )
            # Two distinct new kinds, neither colliding with the earlier seven.
            assert len(set(new_kinds.values())) == 2, new_kinds
            assert existing.isdisjoint(set(new_kinds.values())), (
                f"read-switch kinds {new_kinds} collide with earlier kinds "
                f"{existing}"
            )

    def test_posting_kind_enum_matches_db(self, app, db):
        """Every PostingKindEnum member has a DB row and vice versa.

        Step 2 seeds 'transfer'; Step 3 adds 'income'/'expense'; Step 4 adds
        the four loan-correction kinds; the loan read switch adds
        'opening'/'trueup'.  This guards against a future kind added to the
        enum without a seed row (or vice versa).
        """
        with app.app_context():
            db_rows = db.session.query(PostingKind).all()
            db_names = {row.name for row in db_rows}
            enum_values = {member.value for member in PostingKindEnum}

            assert db_names == enum_values, (
                f"PostingKind DB rows {db_names} do not match "
                f"PostingKindEnum values {enum_values}"
            )
            assert len(db_rows) == len(PostingKindEnum), (
                f"PostingKind has {len(db_rows)} rows but PostingKindEnum "
                f"has {len(PostingKindEnum)} members"
            )


class TestPostingSourceRefCache:
    """Tests for ref_cache posting-source resolution."""

    def test_posting_source_ref_cache(self, app, db):
        """ref_cache.posting_source_id() returns a positive int for TRANSFER_MOVEMENT."""
        with app.app_context():
            source_id = ref_cache.posting_source_id(
                PostingSourceEnum.TRANSFER_MOVEMENT
            )
            assert isinstance(source_id, int), (
                f"posting_source_id(TRANSFER_MOVEMENT) returned "
                f"{type(source_id)}, expected int"
            )
            assert source_id > 0, (
                f"TRANSFER_MOVEMENT id should be positive, got {source_id}"
            )

    def test_posting_source_resolves_loan_payment(self, app, db):
        """ref_cache resolves the Step-4 LOAN_PAYMENT source.

        The loan-payment correction source added in Build-Order Step 4 must
        resolve to a positive ID, guarding that
        ``PostingSourceEnum.LOAN_PAYMENT.value`` matches its seeded
        ``ref.posting_sources.name`` row.  The two sources it was held
        distinct from, Step 2's TRANSFER and Step 3's TRANSACTION, are gone
        (plan step ``balance:X-bi-6-5``'s second leaf, card plan#1845); its
        distinctness from the sources that remain is the read-switch case's
        below and ``test_posting_source_enum_matches_db``'s.
        """
        with app.app_context():
            loan_payment_id = ref_cache.posting_source_id(
                PostingSourceEnum.LOAN_PAYMENT
            )
            assert loan_payment_id > 0, (
                f"loan_payment source id must be positive, got {loan_payment_id}"
            )

    def test_posting_source_resolves_loan_opening_and_trueup(self, app, db):
        """ref_cache resolves the read-switch LOAN_OPENING/LOAN_TRUEUP sources.

        The two loan-genesis source events added by the loan read switch (the
        deferred second half of Build-Order Step 4) must each resolve to a
        distinct positive ID, distinct from one another and from Step 4's
        LOAN_PAYMENT (Step 2's TRANSFER and Step 3's TRANSACTION, held apart
        here too until plan step ``balance:X-bi-6-5``'s second leaf, card
        plan#1845, are gone).  This guards
        that the enum ``.value`` strings match the seeded
        ``ref.posting_sources.name`` rows and that no two members collapse onto
        one row (a copy-paste value collision).
        """
        with app.app_context():
            existing = {
                ref_cache.posting_source_id(member)
                for member in (
                    PostingSourceEnum.LOAN_PAYMENT,
                )
            }
            new_sources = {
                member: ref_cache.posting_source_id(member)
                for member in (
                    PostingSourceEnum.LOAN_OPENING,
                    PostingSourceEnum.LOAN_TRUEUP,
                )
            }
            for member, source_id in new_sources.items():
                assert isinstance(source_id, int) and source_id > 0, (
                    f"{member.name} id must be a positive int, got {source_id}"
                )
            # Two distinct new sources, neither colliding with the earlier one.
            assert len(set(new_sources.values())) == 2, new_sources
            assert existing.isdisjoint(set(new_sources.values())), (
                f"read-switch sources {new_sources} collide with earlier "
                f"sources {existing}"
            )

    def test_posting_source_enum_matches_db(self, app, db):
        """Every PostingSourceEnum member has a DB row and vice versa."""
        with app.app_context():
            db_rows = db.session.query(PostingSource).all()
            db_names = {row.name for row in db_rows}
            enum_values = {member.value for member in PostingSourceEnum}

            assert db_names == enum_values, (
                f"PostingSource DB rows {db_names} do not match "
                f"PostingSourceEnum values {enum_values}"
            )
            assert len(db_rows) == len(PostingSourceEnum), (
                f"PostingSource has {len(db_rows)} rows but PostingSourceEnum "
                f"has {len(PostingSourceEnum)} members"
            )


class TestLedgerAccountKindRefCache:
    """Tests for ref_cache ledger-account-kind resolution (Step 4 discriminator)."""

    def test_ledger_account_kind_ref_cache(self, app, db):
        """ref_cache.ledger_account_kind_id() returns a distinct positive int
        for every LedgerAccountKindEnum member.

        The explicit row-kind discriminator added in Build-Order Step 4 must
        resolve every member (the four existing chart kinds plus the three
        per-loan kinds) to a distinct positive ID, guarding that the enum
        ``.value`` strings match the seeded ``ref.ledger_account_kinds.name``
        rows and that no two members collapse onto one row.
        """
        with app.app_context():
            ids = {
                member: ref_cache.ledger_account_kind_id(member)
                for member in LedgerAccountKindEnum
            }
            for member, kind_id in ids.items():
                assert isinstance(kind_id, int), (
                    f"ledger_account_kind_id({member.name}) returned "
                    f"{type(kind_id)}, expected int"
                )
                assert kind_id > 0, (
                    f"{member.name} id should be positive, got {kind_id}"
                )
            assert len(set(ids.values())) == len(LedgerAccountKindEnum), (
                f"LedgerAccountKindEnum members must have distinct IDs; "
                f"got {ids}"
            )

    def test_ledger_account_kind_enum_matches_db(self, app, db):
        """Every LedgerAccountKindEnum member has exactly one DB row and
        every DB row has a member.  No extra rows, no missing rows.
        """
        with app.app_context():
            db_rows = db.session.query(LedgerAccountKind).all()
            db_names = {row.name for row in db_rows}
            enum_values = {member.value for member in LedgerAccountKindEnum}

            assert db_names == enum_values, (
                f"LedgerAccountKind DB rows {db_names} do not match "
                f"LedgerAccountKindEnum values {enum_values}"
            )
            assert len(db_rows) == len(LedgerAccountKindEnum), (
                f"LedgerAccountKind has {len(db_rows)} rows but "
                f"LedgerAccountKindEnum has {len(LedgerAccountKindEnum)} "
                f"members"
            )

    def test_ref_cache_fails_on_missing_ledger_account_kind(self, app, db):
        """ref_cache.init() raises RuntimeError when a kind row is missing.

        Proves the enum<->seed parity gate fires for the new discriminator
        ref table exactly as it does for the long-standing Status table:
        deleting the 'linked' row leaves LedgerAccountKindEnum.LINKED
        unresolvable, which must be a fatal startup error, not a silent skip.
        """
        with app.app_context():
            linked = (
                db.session.query(LedgerAccountKind)
                .filter_by(name="linked")
                .one()
            )
            db.session.delete(linked)
            db.session.flush()

            with pytest.raises(RuntimeError, match="linked"):
                ref_cache.init(db.session)

            # Roll back so other tests aren't affected, then re-init clean.
            db.session.rollback()
            ref_cache.init(db.session)


class TestTwoAxisRecurrenceRefCache:
    """The two-axis recurrence vocabulary resolves (redesign step R2a).

    ``ref.recurrence_units`` / ``ref.period_placements`` /
    ``ref.business_day_shifts`` are the tables the redesign expresses a rule
    in: ``every <interval_n> <unit>``, placed onto a pay period by
    ``<placement>``, with a weekend/holiday ``<shift>``.  Nothing reads them
    until step R2b adds the FKs, so what these tests protect is the dual-seed
    contract: the migration seeded rows whose ``name`` matches every enum
    ``.value``, and ``ref_cache`` resolves each to a distinct positive ID.

    The parity is asserted per-table and in BOTH directions (no missing row,
    no extra row) because a value present in the enum but absent from the
    table is a fatal ``RuntimeError`` at app start, and a row present in the
    table but absent from the enum is a value no reader can ever resolve.
    """

    def test_recurrence_unit_ids_are_distinct_and_positive(self, app, db):
        """Every RecurrenceUnitEnum member resolves to its own positive ID.

        A collision here would silently collapse two cadences onto one row --
        e.g. ``month`` and ``year`` resolving to the same ID would make an
        annual rule generate monthly, which is 12x the money.
        """
        with app.app_context():
            ids = {
                member: ref_cache.recurrence_unit_id(member)
                for member in RecurrenceUnitEnum
            }
            for member, unit_id in ids.items():
                assert isinstance(unit_id, int) and unit_id > 0, (
                    f"recurrence_unit_id({member.name}) must be a positive "
                    f"int, got {unit_id!r}"
                )
            assert len(set(ids.values())) == len(RecurrenceUnitEnum), (
                f"RecurrenceUnitEnum members must have distinct IDs; got {ids}"
            )

    def test_period_placement_ids_are_distinct_and_positive(self, app, db):
        """Every PeriodPlacementEnum member resolves to its own positive ID.

        The two placements are the ONLY difference between today's Monthly and
        Monthly First patterns, so a collision would merge two user-visible
        behaviours into one.
        """
        with app.app_context():
            ids = {
                member: ref_cache.period_placement_id(member)
                for member in PeriodPlacementEnum
            }
            for member, placement_id in ids.items():
                assert isinstance(placement_id, int) and placement_id > 0, (
                    f"period_placement_id({member.name}) must be a positive "
                    f"int, got {placement_id!r}"
                )
            assert len(set(ids.values())) == len(PeriodPlacementEnum), (
                f"PeriodPlacementEnum members must have distinct IDs; got {ids}"
            )

    def test_business_day_shift_ids_are_distinct_and_positive(self, app, db):
        """Every BusinessDayShiftEnum member resolves to its own positive ID.

        ``none`` colliding with ``prior`` or ``next`` would move a bill's cash
        date off the day the user chose.
        """
        with app.app_context():
            ids = {
                member: ref_cache.business_day_shift_id(member)
                for member in BusinessDayShiftEnum
            }
            for member, shift_id in ids.items():
                assert isinstance(shift_id, int) and shift_id > 0, (
                    f"business_day_shift_id({member.name}) must be a positive "
                    f"int, got {shift_id!r}"
                )
            assert len(set(ids.values())) == len(BusinessDayShiftEnum), (
                f"BusinessDayShiftEnum members must have distinct IDs; "
                f"got {ids}"
            )

    def test_recurrence_unit_enum_matches_db(self, app, db):
        """``ref.recurrence_units`` holds exactly the enum's values."""
        with app.app_context():
            db_names = {row.name for row in db.session.query(RecurrenceUnit).all()}
            enum_values = {member.value for member in RecurrenceUnitEnum}
            assert db_names == enum_values, (
                f"RecurrenceUnit DB rows {sorted(db_names)} do not match "
                f"RecurrenceUnitEnum values {sorted(enum_values)}"
            )

    def test_period_placement_enum_matches_db(self, app, db):
        """``ref.period_placements`` holds exactly the enum's values."""
        with app.app_context():
            db_names = {row.name for row in db.session.query(PeriodPlacement).all()}
            enum_values = {member.value for member in PeriodPlacementEnum}
            assert db_names == enum_values, (
                f"PeriodPlacement DB rows {sorted(db_names)} do not match "
                f"PeriodPlacementEnum values {sorted(enum_values)}"
            )

    def test_business_day_shift_enum_matches_db(self, app, db):
        """``ref.business_day_shifts`` holds exactly the enum's values."""
        with app.app_context():
            db_names = {
                row.name for row in db.session.query(BusinessDayShift).all()
            }
            enum_values = {member.value for member in BusinessDayShiftEnum}
            assert db_names == enum_values, (
                f"BusinessDayShift DB rows {sorted(db_names)} do not match "
                f"BusinessDayShiftEnum values {sorted(enum_values)}"
            )

    def test_ref_cache_fails_on_missing_recurrence_unit(self, app, db):
        """``ref_cache.init()`` raises when a recurrence-unit row is missing.

        Proves the enum<->seed parity gate fires for the new vocabulary
        exactly as it does for the long-standing Status table: deleting the
        ``month`` row leaves ``RecurrenceUnitEnum.MONTH`` unresolvable, which
        must be a fatal startup error rather than a silent skip that would
        later surface as an unresolvable cadence mid-generation.
        """
        with app.app_context():
            month = (
                db.session.query(RecurrenceUnit).filter_by(name="month").one()
            )
            db.session.delete(month)
            db.session.flush()

            with pytest.raises(RuntimeError, match="MONTH"):
                ref_cache.init(db.session)

            # Roll back so other tests aren't affected, then re-init clean.
            db.session.rollback()
            ref_cache.init(db.session)


class TestAcctCategoryMemberRefCache:
    """The account-category REVERSE lookup (plan step X-z6, ruling R-CV).

    ``acct_category_member`` inverts ``acct_category_id`` so the canonical
    classifier (:func:`app.services.account_category.account_category`)
    resolves an account's category in one dict read.  It was a scan over the
    enum asking ``acct_category_id`` once per member, which made every
    net-worth surface's liability question 2.3x-4.5x more expensive than the
    single comparison plan step X-z1 replaced.
    """

    def test_round_trips_every_member(self, app, db):
        """Every member's id resolves back to that member, and only that one.

        The VALUE assertion the inversion could get wrong: a builder that
        collided two members onto one id, or inverted the wrong way, returns a
        different member here rather than a shape difference.
        """
        with app.app_context():
            for member in AcctCategoryEnum:
                category_id = ref_cache.acct_category_id(member)
                assert ref_cache.acct_category_member(category_id) is member, (
                    f"{member.name}: id {category_id} did not round-trip"
                )

    def test_the_inversion_is_total_and_one_to_one(self, app, db):
        """Every cached category id appears exactly once in the reverse map.

        A collision would silently shadow one category -- the reverse map is
        built from a dict comprehension, so two members sharing an id would
        keep only the last and the shadowed category would classify as the
        other one on every surface.
        """
        with app.app_context():
            ids = [
                ref_cache.acct_category_id(member)
                for member in AcctCategoryEnum
            ]
            assert len(set(ids)) == len(ids)
            resolved = {ref_cache.acct_category_member(i) for i in ids}
            assert resolved == set(AcctCategoryEnum)

    def test_an_unmodelled_category_id_answers_none(self, app, db):
        """An id no member names answers ``None`` -- it does NOT raise.

        **This is the deliberate difference from
        ``ledger_class_is_debit_normal`` beside it**, which raises on an
        unknown id.  That accessor reads a CLOSED set this application wrote
        from its own enum, so an unknown id is a data error.  This one reads
        ``ref.account_type_categories``, where ``init`` requires the four
        enum rows and does not forbid a fifth -- so an unmodelled category is a
        state the schema permits, and the application's answer for it is "no
        category I model" (bucketed ``other``, never a liability), not a 500 on
        every page that classifies an account.
        """
        with app.app_context():
            known = {
                ref_cache.acct_category_id(m) for m in AcctCategoryEnum
            }
            assert ref_cache.acct_category_member(max(known) + 1000) is None

    def test_requires_initialization(self, app, db):
        """An uninitialized cache raises rather than answering ``None``.

        The ``None`` above means "no such category"; a ``None`` from an
        unloaded cache would mean "I could not look", and the two must not be
        the same answer -- every account would classify as uncategorised and
        the net-worth hero would report every liability as an asset.
        """
        with app.app_context():
            # The cache itself lives in the package's ``_state`` module since
            # plan step ``bank_import:X-f6e-1`` split ``ref_cache`` in two.
            # Reached directly rather than re-exported: it is private, and a
            # public alias for it would be a second name for the one thing the
            # split exists to keep behind a door.
            # pylint: disable=protected-access
            _state._cache.initialized = False
            try:
                with pytest.raises(RuntimeError):
                    ref_cache.acct_category_member(1)
            finally:
                ref_cache.init(db.session)


class TestRaiseTypeMemberRefCache:
    """The raise-type REVERSE lookup (plan step salary:S3-f-1, ruling R-SAL20).

    ``raise_type_member`` inverts ``raise_type_id`` so
    :attr:`app.models.salary_raise.SalaryRaise.raise_type_name` resolves a
    raise's display name from the FK the row carries rather than from the
    ``raise_type`` relationship -- which is ``None`` on a row that has never
    been flushed, because SQLAlchemy does not lazy-load a relationship on a
    pending instance.  The X-bl invariance control prices exactly such a
    row, and a second adversarial review of S3-f-1 found the relationship
    read breaking it.
    """

    def test_round_trips_every_member(self, app, db):
        """Every member's id resolves back to that member, and only that one."""
        with app.app_context():
            for member in RaiseTypeEnum:
                type_id = ref_cache.raise_type_id(member)
                assert ref_cache.raise_type_member(type_id) is member, (
                    f"{member.name}: id {type_id} did not round-trip"
                )

    def test_the_member_value_is_the_rows_name(self, app, db):
        """``member.value`` IS ``ref.raise_types.name``, which is what makes the
        cache a source of the display name at all."""
        with app.app_context():
            for row in db.session.query(RaiseType).all():
                member = ref_cache.raise_type_member(row.id)
                assert member is not None, f"ref.raise_types row {row.name!r} is not an enum member"
                assert member.value == row.name

    def test_an_unmodelled_type_id_answers_none(self, app, db):
        """An id no member names answers ``None`` -- the caller reads the row's
        own name for a hand-inserted type, never a 500."""
        with app.app_context():
            known = {ref_cache.raise_type_id(m) for m in RaiseTypeEnum}
            assert ref_cache.raise_type_member(max(known) + 1000) is None


class TestFilingStatusMemberRefCache:
    """The filing-status lookup the tax law is reached through (plan step salary:X-at-1).

    ``filing_status_member`` turns a profile's stored ``filing_status_id``
    into the :class:`~app.enums.FilingStatusEnum` member the law
    (:mod:`app.tax_law`) is keyed on, so no reader builds a member out of the
    ref row's ``name``.  A wrong answer here prices a filer on another
    status's brackets.
    """

    def test_every_row_resolves_to_the_member_its_name_is(self, app, db):
        """Each ``ref.filing_statuses`` row resolves to the member whose value is its name."""
        with app.app_context():
            rows = db.session.query(FilingStatus).all()
            assert {row.name for row in rows} == {m.value for m in FilingStatusEnum}
            for row in rows:
                assert ref_cache.filing_status_member(row.id) is FilingStatusEnum(row.name), (
                    f"ref.filing_statuses row {row.name!r} (id {row.id}) resolved wrongly"
                )

    def test_an_unmodelled_status_id_answers_none(self, app, db):
        """An id no member names answers ``None`` -- the caller prices no federal rules."""
        with app.app_context():
            known = {row.id for row in db.session.query(FilingStatus).all()}
            assert ref_cache.filing_status_member(max(known) + 1000) is None

    def test_requires_initialization(self, app, db):
        """An uninitialized cache raises rather than answering ``None``.

        ``None`` means "no status the law models"; from an unloaded cache it
        would mean "I could not look", and every filer would price with no
        federal rules.
        """
        with app.app_context():
            # pylint: disable=protected-access
            _state._cache.initialized = False
            try:
                with pytest.raises(RuntimeError):
                    ref_cache.filing_status_member(1)
            finally:
                ref_cache.init(db.session)


class TestAmountSourceRefCache:
    """The amount model's discriminator resolves (plan step X-au-c1, ruling R-FI).

    ``ref.amount_sources`` names WHICH RELATION states a row's amount when the
    row does not state it itself.  Nothing in ``app/`` declares a source yet --
    the per-kind cutovers (plan steps X-au-d..X-au-i) are the first writers -- so
    what these tests protect is the dual-seed contract and the enum<->row parity
    that ``ref_cache.init()`` turns into a fatal startup error.

    The parity is asserted in BOTH directions: a value in the enum with no row is
    that fatal error, and a row with no member is a value no reader can resolve
    but a writer could still stamp -- which would produce a derived row nothing
    can price.
    """

    def test_ids_are_distinct_and_positive(self, app, db):
        """Every AmountSourceEnum member resolves to its own positive ID.

        A collision would merge two relations into one, so a row priced by its
        parent transfer would be looked up against its template instead -- a
        wrong figure rather than a refusal.
        """
        with app.app_context():
            ids = {
                member: ref_cache.amount_source_id(member)
                for member in AmountSourceEnum
            }
            for member, source_id in ids.items():
                assert isinstance(source_id, int) and source_id > 0, (
                    f"amount_source_id({member.name}) must be a positive int, "
                    f"got {source_id!r}"
                )
            assert len(set(ids.values())) == len(AmountSourceEnum), (
                f"AmountSourceEnum members must have distinct IDs; got {ids}"
            )

    def test_enum_matches_db(self, app, db):
        """``ref.amount_sources`` holds exactly the enum's values.

        There is deliberately no ``own`` row: owning an amount is the ABSENCE of
        a source (``amount_source_id IS NULL``), which is what lets the ownership
        CHECK be written over two NULL-nesses instead of freezing a ref id into
        the schema.  A stray ``own`` row here would be the first sign that
        distinction had been lost.
        """
        with app.app_context():
            db_names = {row.name for row in db.session.query(AmountSource).all()}
            enum_values = {member.value for member in AmountSourceEnum}
            assert db_names == enum_values, (
                f"AmountSource DB rows {sorted(db_names)} do not match "
                f"AmountSourceEnum values {sorted(enum_values)}"
            )

    def test_ref_cache_fails_on_missing_amount_source(self, app, db):
        """``ref_cache.init()`` raises when an amount-source row is missing.

        The parity gate fires for this vocabulary as it does for every other:
        deleting the ``template`` row leaves ``AmountSourceEnum.TEMPLATE``
        unresolvable, which must be a fatal startup error rather than a silent
        skip that would later surface as an unstampable declaration mid-cutover.
        """
        with app.app_context():
            template_row = (
                db.session.query(AmountSource).filter_by(name="template").one()
            )
            db.session.delete(template_row)
            db.session.flush()

            with pytest.raises(RuntimeError, match="TEMPLATE"):
                ref_cache.init(db.session)

            # Roll back so other tests aren't affected, then re-init clean.
            db.session.rollback()
            ref_cache.init(db.session)


class TestThePackageReExportsItsWholeSurface:
    """``app.ref_cache`` is a PACKAGE, and ``__all__`` is what callers get.

    Plan step **bank_import:X-f6e-1** split the flat module in two when a
    twenty-sixth reference table took it past pylint's 1,000-line ceiling.
    The split's whole guarantee is that no caller changed, and that rests on
    one hand-written re-export block in ``__init__.py``.

    **Nothing pinned that block, and two of the names it carries have no
    caller anywhere to raise an alarm** -- ``acct_type_icon`` and
    ``acct_type_max_term`` are measurably dead, 0 references in ``app/``,
    ``tests/``, ``scripts/`` or ``tools/``.  Dropping either from the block
    would leave the whole ~5,500-test suite green and ship the hole.  Found by
    an adversarial review of the split, 2026-08-23.
    """

    def test_every_accessor_the_package_defines_is_re_exported(self):
        """Derived from the module, never a hand-kept list.

        A hardcoded roster would catch a DROPPED name and miss an ADDED one --
        a twenty-seventh accessor written and never re-exported is exactly the
        same hole, arriving from the other direction.
        """
        defined = {
            name for name, value in vars(_accessors).items()
            if not name.startswith("_")
            and getattr(value, "__module__", None) == _accessors.__name__
        }

        assert defined, "no accessors found -- the reflection is wrong"
        assert defined | {"init"} == set(ref_cache.__all__)
        for name in defined:
            assert hasattr(ref_cache, name), f"{name} is not re-exported"

    def test_the_two_dead_accessors_are_reachable(self):
        """Named because reflection would not miss them and a human would.

        These are the two the class docstring calls out.  They are asserted
        BY NAME so that the day someone deletes one -- which finding
        **N-341**'s step may well do -- this test says so out loud instead of
        adjusting silently along with the reflection above.
        """
        assert hasattr(ref_cache, "acct_type_icon")
        assert hasattr(ref_cache, "acct_type_max_term")

    def test_the_package_INTERNAL_seam_is_not_re_exported(self):
        """``cache`` and ``require_init`` are public to the package only.

        They are public names in a PRIVATE module: that is what lets
        ``_accessors`` read the cache by name without thirty-one
        ``protected-access`` disables, and ``shekel-private-module-import`` is
        what stops the world reaching them.  Exporting either here would undo
        that and hand callers a door onto the cache object itself.
        """
        assert "cache" not in ref_cache.__all__
        assert "require_init" not in ref_cache.__all__
        assert not hasattr(ref_cache, "cache")
        assert not hasattr(ref_cache, "require_init")
