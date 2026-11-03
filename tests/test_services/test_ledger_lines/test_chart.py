"""Tests for the chart worked out from the records (plan task plan#2523).

Ruling plan#2518 works the chart of accounts out instead of storing it: a
ledger line names a :class:`~app.services.ledger_lines.ChartKey` -- its kind,
its class and the record behind it -- and two pure rules answer its class and
its label.  These tests build, for every kind the records can name, the key
from a fixture's RECORDS and grade it against the row today's writer stores
for the same thing (``ledger_account_service``): the same kind, the same
class, the key the stored row's own columns spell (the mapping the parity test
plan#2527 maps by), and the same label a statement shows today
(``ledger_report_service._attribution.ledger_account_label``).  Every expected
class and label is written out by hand, so the writer -- which stamps its rows
from these same rules since this task -- is never its own grader.

Then the ruled difference (a rename relabels a worked-out line, where a stored
snapshot kept its minted name), the deleted category no key names, and the
refusals the constructors and the label rule raise.
"""

import pytest

from app import ref_cache
from app.enums import LedgerAccountClassEnum, LedgerAccountKindEnum
from app.extensions import db as _db
from app.models.category import Category
from app.models.ledger_account import LedgerAccount
from app.models.transaction import Transaction
from app.services import ledger_account_service
from app.services.ledger_lines import (
    ChartKey,
    account_counter_key,
    category_key,
    chart_label,
    ledger_class_of,
    linked_key,
    loan_key,
    owner_bucket_key,
    transit_key,
)
# The label a statement shows for a STORED row today is package-private to the
# report (its one reader); the test grades the worked-out label against it, so
# it reaches the private module by name rather than asking for a re-export that
# would exist only for a test (the same posture as
# ``test_ledger_report_service``'s import of ``dated_account_nets``).
from app.services.ledger_report_service._attribution import ledger_account_label
from tests._test_helpers import (
    add_txn,
    create_account_of_type,
    linked_ledger_account,
    stored_chart_key,
)


def _assert_key_matches_stored_row(stored, key, record, expected_label):
    """Assert *key* and its label agree with the *stored* row the writer made.

    Args:
        stored: The :class:`LedgerAccount` today's writer stores for the line.
        key: The key worked out from the records.
        record: The record *key* names (an account, a category, or ``None``).
        expected_label: The hand-written label a statement shows.
    """
    assert stored.kind_id == ref_cache.ledger_account_kind_id(key.kind)
    assert stored.class_id == ref_cache.ledger_account_class_id(
        key.ledger_class,
    )
    assert stored_chart_key(stored) == key
    assert chart_label(key, record) == expected_label
    assert ledger_account_label(stored) == expected_label


class TestEveryKindMatchesTheRowTheWriterStores:
    """One test per kind the records can name, class and label by hand."""

    @pytest.mark.parametrize("type_name,expected_class", [
        ("Checking", LedgerAccountClassEnum.ASSET),
        ("HYSA", LedgerAccountClassEnum.ASSET),
        ("Roth IRA", LedgerAccountClassEnum.ASSET),
        ("Brokerage", LedgerAccountClassEnum.ASSET),
        ("Credit Card", LedgerAccountClassEnum.LIABILITY),
        ("Mortgage", LedgerAccountClassEnum.LIABILITY),
    ])
    def test_an_accounts_own_line_is_classed_by_its_types_category(
        self, app, db, seed_user, type_name, expected_class,
    ):
        """A linked key is Liability for borrowed money, Asset for the rest.

        Asset, Retirement and Investment categories are all Asset class; only
        the Liability category (a card, a loan) is Liability.  The label is
        the account's own name, and the stored linked row carries no name of
        its own (it reads the account's too).
        """
        with app.app_context():
            account = create_account_of_type(
                seed_user, _db.session, type_name, "Chart Own Line",
            )

            key = linked_key(account)

            assert key == ChartKey(
                LedgerAccountKindEnum.LINKED, expected_class, account.id,
            )
            stored = linked_ledger_account(_db.session, account.id)
            assert stored.name is None
            _assert_key_matches_stored_row(
                stored, key, account, "Chart Own Line",
            )

    @pytest.mark.parametrize("is_income,expected_class", [
        (True, LedgerAccountClassEnum.INCOME),
        (False, LedgerAccountClassEnum.EXPENSE),
    ])
    def test_a_category_line_is_classed_by_its_rows_type(
        self, app, db, seed_user, seed_periods, is_income, expected_class,
    ):
        """A category key takes the class its source row's type gives.

        The class is part of the category line's identity: a category is
        type-agnostic, so an income row and an expense row in "Family:
        Groceries" book on two keys.  The label is the category's
        ``"Group: Item"``.
        """
        with app.app_context():
            groceries = seed_user["categories"]["Groceries"]
            txn = add_txn(
                _db.session, seed_user, seed_periods[0], "Chart Category Row",
                "40.00", is_income=is_income, category_key="Groceries",
            )

            key = category_key(txn.category_id, ledger_class_of(txn))

            assert key == ChartKey(
                LedgerAccountKindEnum.CATEGORY, expected_class, groceries.id,
            )
            stored = ledger_account_service.get_or_create_category_ledger_account(
                seed_user["user"].id, groceries.id, expected_class,
            )
            _assert_key_matches_stored_row(
                stored, key, groceries, "Family: Groceries",
            )

    @pytest.mark.parametrize("is_income,expected_class,expected_label", [
        (True, LedgerAccountClassEnum.INCOME, "Uncategorized Income"),
        (False, LedgerAccountClassEnum.EXPENSE, "Uncategorized Expense"),
    ])
    def test_a_row_with_no_category_books_on_the_uncategorized_bucket(
        self, app, db, seed_user, seed_periods, is_income, expected_class,
        expected_label,
    ):
        """No category is the owner's Uncategorized bucket in the row's class."""
        with app.app_context():
            txn = add_txn(
                _db.session, seed_user, seed_periods[0], "Chart Bare Row",
                "15.00", is_income=is_income,
            )
            assert txn.category_id is None

            key = category_key(txn.category_id, ledger_class_of(txn))

            assert key == ChartKey(
                LedgerAccountKindEnum.FALLBACK, expected_class, None,
            )
            stored = ledger_account_service.get_or_create_category_ledger_account(
                seed_user["user"].id, None, expected_class,
            )
            _assert_key_matches_stored_row(stored, key, None, expected_label)

    def test_transit_is_the_owners_asset_bucket(self, app, db, seed_user):
        """Transfers in transit is an Asset bucket with no record behind it."""
        with app.app_context():
            key = transit_key()

            assert key == ChartKey(
                LedgerAccountKindEnum.TRANSIT,
                LedgerAccountClassEnum.ASSET,
                None,
            )
            stored = ledger_account_service.get_or_create_transit_ledger_account(
                seed_user["user"].id,
            )
            _assert_key_matches_stored_row(
                stored, key, None, "Transfers in transit",
            )

    @pytest.mark.parametrize("kind,expected_class,component", [
        (LedgerAccountKindEnum.ANCHOR_EQUITY,
         LedgerAccountClassEnum.EQUITY, "Opening"),
        (LedgerAccountKindEnum.INTEREST_INCOME,
         LedgerAccountClassEnum.INCOME, "Interest Income"),
        (LedgerAccountKindEnum.UNREALIZED_CHANGE,
         LedgerAccountClassEnum.UNREALIZED, "Change in Value"),
    ])
    def test_an_accounts_counter_line_takes_its_kinds_class(
        self, app, db, seed_user, kind, expected_class, component,
    ):
        """Each correction counter is classed by its kind, labelled by its account."""
        with app.app_context():
            account = create_account_of_type(
                seed_user, _db.session, "Checking", "Chart Counter",
            )

            key = account_counter_key(kind, account.id)

            assert key == ChartKey(kind, expected_class, account.id)
            stored = ledger_account_service.get_or_create_account_counter_account(
                seed_user["user"].id, account.id, kind,
            )
            _assert_key_matches_stored_row(
                stored, key, account, f"Chart Counter -- {component}",
            )

    @pytest.mark.parametrize("kind,expected_class,component", [
        (LedgerAccountKindEnum.LOAN_INTEREST,
         LedgerAccountClassEnum.EXPENSE, "Interest"),
        (LedgerAccountKindEnum.LOAN_ESCROW,
         LedgerAccountClassEnum.EXPENSE, "Escrow"),
        (LedgerAccountKindEnum.LOAN_REFUND,
         LedgerAccountClassEnum.ASSET, "Refund"),
        (LedgerAccountKindEnum.EQUITY_OPENING,
         LedgerAccountClassEnum.EQUITY, "Opening"),
    ])
    def test_a_loans_component_line_takes_its_kinds_class(
        self, app, db, seed_user, kind, expected_class, component,
    ):
        """Each loan component is classed by its kind, labelled by its loan."""
        with app.app_context():
            loan = create_account_of_type(
                seed_user, _db.session, "Mortgage", "Chart Loan",
            )

            key = loan_key(kind, loan.id)

            assert key == ChartKey(kind, expected_class, loan.id)
            stored = ledger_account_service.get_or_create_loan_ledger_account(
                seed_user["user"].id, loan.id, kind,
            )
            _assert_key_matches_stored_row(
                stored, key, loan, f"Chart Loan -- {component}",
            )


class TestLabelsAreTodaysNames:
    """Ruling plan#2518: "Statements show current names"."""

    def test_a_rename_relabels_counter_and_loan_lines_the_store_kept(
        self, app, db, seed_user,
    ):
        """A renamed account's counter and loan lines read the new name.

        The stored counter and loan rows snapshotted the name they were minted
        with and keep it; the worked-out label follows the rename.  This is
        the one difference the ruling chose, shown on both families.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            account = create_account_of_type(
                seed_user, _db.session, "Checking", "Old Checking",
            )
            loan = create_account_of_type(
                seed_user, _db.session, "Mortgage", "Old Loan",
            )
            counter_row = (
                ledger_account_service.get_or_create_account_counter_account(
                    user_id, account.id, LedgerAccountKindEnum.ANCHOR_EQUITY,
                )
            )
            loan_row = ledger_account_service.get_or_create_loan_ledger_account(
                user_id, loan.id, LedgerAccountKindEnum.LOAN_INTEREST,
            )

            account.name = "New Checking"
            loan.name = "New Loan"
            _db.session.flush()

            assert ledger_account_label(counter_row) == "Old Checking -- Opening"
            assert ledger_account_label(loan_row) == "Old Loan -- Interest"
            assert chart_label(
                account_counter_key(
                    LedgerAccountKindEnum.ANCHOR_EQUITY, account.id,
                ),
                account,
            ) == "New Checking -- Opening"
            assert chart_label(
                loan_key(LedgerAccountKindEnum.LOAN_INTEREST, loan.id), loan,
            ) == "New Loan -- Interest"

    def test_a_long_category_label_is_not_clipped(self, app, db, seed_user):
        """The label is the whole ``"Group: Item"``, as a statement shows today.

        The stored snapshot is clipped to its 100-character column; the
        worked-out label has no column, and equals the live label today's
        statement already reads for a category row.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            long_category = Category(
                user_id=user_id, group_name="G" * 100, item_name="I" * 100,
            )
            _db.session.add(long_category)
            _db.session.flush()
            stored = ledger_account_service.get_or_create_category_ledger_account(
                user_id, long_category.id, LedgerAccountClassEnum.EXPENSE,
            )

            label = chart_label(
                category_key(long_category.id, LedgerAccountClassEnum.EXPENSE),
                long_category,
            )

            # 100 + len(": ") + 100 = 202, against the snapshot's 100.
            assert label == "G" * 100 + ": " + "I" * 100
            assert len(stored.name) == 100
            assert ledger_account_label(stored) == label


class TestADeletedCategoryIsNamedByNoKey:
    """The stored ``category`` row outlives its category; no key names it."""

    def test_its_rows_book_on_the_uncategorized_bucket(
        self, app, db, seed_user, seed_periods,
    ):
        """After the delete the row reads no category, so it is Uncategorized.

        The stored row keeps its kind and loses its ``category_id`` (SET
        NULL), so its columns spell a category key with no record, which no
        constructor builds; the transaction's own ``category_id`` is SET NULL
        too, so worked out from the records its line is the Uncategorized
        Expense bucket's.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            # A category of the test's own: the seeded ones are named by the
            # seeded templates, whose RESTRICT foreign key refuses the delete.
            doomed = Category(
                user_id=user_id, group_name="Chart", item_name="Doomed",
            )
            _db.session.add(doomed)
            _db.session.flush()
            txn = add_txn(
                _db.session, seed_user, seed_periods[0], "Chart Orphaned Row",
                "25.00",
            )
            txn.category_id = doomed.id
            stored = ledger_account_service.get_or_create_category_ledger_account(
                user_id, doomed.id, LedgerAccountClassEnum.EXPENSE,
            )
            txn_id, stored_id = txn.id, stored.id
            _db.session.commit()

            _db.session.execute(_db.text(
                "DELETE FROM budget.categories WHERE id = :c"
            ), {"c": doomed.id})
            _db.session.commit()
            _db.session.expire_all()

            stored = _db.session.get(LedgerAccount, stored_id)
            assert stored_chart_key(stored) == ChartKey(
                LedgerAccountKindEnum.CATEGORY,
                LedgerAccountClassEnum.EXPENSE,
                None,
            )
            txn = _db.session.get(Transaction, txn_id)
            assert category_key(
                txn.category_id, ledger_class_of(txn),
            ) == ChartKey(
                LedgerAccountKindEnum.FALLBACK,
                LedgerAccountClassEnum.EXPENSE,
                None,
            )


class TestTheConstructorsRefuseWhatNoChartAccountIs:
    """Each constructor refuses a kind or class its family cannot carry."""

    @pytest.mark.parametrize("ledger_class", [
        LedgerAccountClassEnum.ASSET,
        LedgerAccountClassEnum.LIABILITY,
        LedgerAccountClassEnum.EQUITY,
        LedgerAccountClassEnum.UNREALIZED,
    ])
    def test_a_category_line_is_income_or_expense(self, ledger_class):
        """A category key in any other class is refused."""
        with pytest.raises(ValueError, match="Income or Expense"):
            category_key(1, ledger_class)

    @pytest.mark.parametrize("kind,ledger_class", [
        (LedgerAccountKindEnum.FALLBACK, LedgerAccountClassEnum.ASSET),
        (LedgerAccountKindEnum.TRANSIT, LedgerAccountClassEnum.INCOME),
        (LedgerAccountKindEnum.LINKED, LedgerAccountClassEnum.ASSET),
    ])
    def test_a_bucket_is_one_the_chart_names(self, kind, ledger_class):
        """A ``(kind, class)`` pair that is no bucket is refused."""
        with pytest.raises(ValueError, match="no owner bucket is defined"):
            owner_bucket_key(kind, ledger_class)

    @pytest.mark.parametrize("kind", [
        LedgerAccountKindEnum.LINKED,
        LedgerAccountKindEnum.LOAN_INTEREST,
        LedgerAccountKindEnum.EQUITY_OPENING,
    ])
    def test_a_counter_line_is_a_counter_kind(self, kind):
        """A counter key of a non-counter kind is refused."""
        with pytest.raises(ValueError, match="counter ledger account kind"):
            account_counter_key(kind, 1)

    @pytest.mark.parametrize("kind", [
        LedgerAccountKindEnum.LINKED,
        LedgerAccountKindEnum.ANCHOR_EQUITY,
        LedgerAccountKindEnum.CATEGORY,
    ])
    def test_a_loan_line_is_a_loan_kind(self, kind):
        """A loan key of a non-loan kind is refused."""
        with pytest.raises(ValueError, match="loan ledger account kind"):
            loan_key(kind, 1)


class TestTheLabelRefusesARecordTheKeyDoesNotName:
    """A record that is not the key's would label the line with a wrong name."""

    def test_another_type_another_id_or_a_record_for_a_bucket(
        self, app, db, seed_user,
    ):
        """Each mismatch raises, naming the key and what it was handed."""
        with app.app_context():
            account = create_account_of_type(
                seed_user, _db.session, "Checking", "Chart Mismatch",
            )
            other = create_account_of_type(
                seed_user, _db.session, "Savings", "Chart Other",
            )
            groceries = seed_user["categories"]["Groceries"]
            key = linked_key(account)

            with pytest.raises(
                ValueError, match=f"names the Account id={account.id}",
            ):
                chart_label(key, groceries)
            with pytest.raises(
                ValueError, match=f"names the Account id={account.id}",
            ):
                chart_label(key, other)
            with pytest.raises(ValueError, match="names no record"):
                chart_label(transit_key(), account)
            with pytest.raises(
                ValueError, match=f"names the Category id={groceries.id}",
            ):
                chart_label(
                    category_key(
                        groceries.id, LedgerAccountClassEnum.EXPENSE,
                    ),
                    None,
                )

    def test_a_key_no_constructor_builds_has_no_label(self):
        """An ``orphan`` key, spelled by hand, has no label rule."""
        with pytest.raises(ValueError, match="no chart label rule"):
            chart_label(
                ChartKey(
                    LedgerAccountKindEnum.ORPHAN,
                    LedgerAccountClassEnum.EXPENSE,
                    None,
                ),
                None,
            )
