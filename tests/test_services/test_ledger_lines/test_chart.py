"""Tests for the chart worked out from the records (plan task plan#2523).

Ruling plan#2518 works the chart of accounts out instead of storing it: a
ledger line names a :class:`~app.services.ledger_lines.ChartKey` -- its kind,
its class and the record behind it -- and two pure rules answer its class and
its label.  These tests build, for every kind the records can name, the key
from a fixture's RECORDS and grade its kind, its class and the label a
statement shows (:func:`chart_label`'s since plan task plan#2771), each
written out by hand.  Until plan step plan#2914 they also graded the key
against the row the store's writer (``ledger_account_service``) kept for the
same thing; that grade went with the store.

Then the ruled difference (a rename relabels a worked-out line, where the
store's rows kept the name they were minted with), the deleted category no key
names, the refusals the constructors and the label rule raise, and ruling R-FO's
dispatch of what a true-up's difference was, which moved here from the chart
writer's own tests when the writer went (plan#2914).
"""

import pytest

from app.enums import LedgerAccountClassEnum, LedgerAccountKindEnum
from app.extensions import db as _db
from app.models.account import Account
from app.models.category import Category
from app.models.transaction import Transaction
from app.services import ledger_lines
from app.services.account_projection import AccountProjectionKind
from app.services.ledger_lines import (
    ChartKey,
    account_counter_key,
    category_key,
    chart_label,
    chart_record_type,
    ledger_class_of,
    linked_key,
    loan_key,
    owner_bucket_key,
    transit_key,
)
from tests._test_helpers import (
    add_txn,
    create_account_of_type,
)


class TestEveryKindIsClassedAndLabelledByHand:
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
        the account's own name.
        """
        with app.app_context():
            account = create_account_of_type(
                seed_user, _db.session, type_name, "Chart Own Line",
            )

            key = linked_key(account)

            assert key == ChartKey(
                LedgerAccountKindEnum.LINKED, expected_class, account.id,
            )
            assert chart_label(key, account) == "Chart Own Line"

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
            assert chart_label(key, groceries) == "Family: Groceries"

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
            assert chart_label(key, None) == expected_label

    def test_transit_is_the_owners_asset_bucket(self, app, db):
        """Transfers in transit is an Asset bucket with no record behind it."""
        with app.app_context():
            key = transit_key()

            assert key == ChartKey(
                LedgerAccountKindEnum.TRANSIT,
                LedgerAccountClassEnum.ASSET,
                None,
            )
            assert chart_label(key, None) == "Transfers in transit"

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
            assert chart_label(key, account) == f"Chart Counter -- {component}"

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
            assert chart_label(key, loan) == f"Chart Loan -- {component}"


class TestLabelsAreTodaysNames:
    """Ruling plan#2518: "Statements show current names"."""

    def test_a_rename_relabels_counter_and_loan_lines(
        self, app, seed_user,
    ):
        """A renamed account's counter and loan lines read the new name.

        The store's counter and loan rows snapshotted the name they were minted
        with and kept it; the worked-out label follows the rename.  This is
        the one difference the ruling chose, shown on both families.
        """
        with app.app_context():
            account = create_account_of_type(
                seed_user, _db.session, "Checking", "Old Checking",
            )
            loan = create_account_of_type(
                seed_user, _db.session, "Mortgage", "Old Loan",
            )

            account.name = "New Checking"
            loan.name = "New Loan"
            _db.session.flush()

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

        The store's snapshot was clipped to its 100-character column; the
        worked-out label has no column, and is the live ``"Group: Item"`` a
        statement shows.
        """
        with app.app_context():
            long_category = Category(
                user_id=seed_user["user"].id,
                group_name="G" * 100, item_name="I" * 100,
            )
            _db.session.add(long_category)
            _db.session.flush()

            label = chart_label(
                category_key(long_category.id, LedgerAccountClassEnum.EXPENSE),
                long_category,
            )

            # 100 + len(": ") + 100 = 202 characters, none clipped.
            assert label == "G" * 100 + ": " + "I" * 100


class TestADeletedCategoryIsNamedByNoKey:
    """A deleted category is named by no key; its rows' lines are Uncategorized."""

    def test_its_rows_book_on_the_uncategorized_bucket(
        self, app, db, seed_user, seed_periods,
    ):
        """After the delete the row reads no category, so it is Uncategorized.

        The transaction's ``category_id`` is SET NULL by the delete, so worked
        out from the records its line is the Uncategorized Expense bucket's.
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
            txn_id = txn.id
            _db.session.commit()

            _db.session.execute(_db.text(
                "DELETE FROM budget.categories WHERE id = :c"
            ), {"c": doomed.id})
            _db.session.commit()
            _db.session.expire_all()

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


class TestEachKeyNamesOneTypeOfRecord:
    """:func:`chart_record_type`: the table a key's label is read from (plan#2771)."""

    @pytest.mark.parametrize("key,expected", [
        (ChartKey(
            LedgerAccountKindEnum.LINKED, LedgerAccountClassEnum.ASSET, 1,
        ), Account),
        (account_counter_key(LedgerAccountKindEnum.ANCHOR_EQUITY, 1), Account),
        (account_counter_key(LedgerAccountKindEnum.INTEREST_INCOME, 1), Account),
        (account_counter_key(
            LedgerAccountKindEnum.UNREALIZED_CHANGE, 1,
        ), Account),
        (loan_key(LedgerAccountKindEnum.LOAN_INTEREST, 1), Account),
        (loan_key(LedgerAccountKindEnum.LOAN_ESCROW, 1), Account),
        (loan_key(LedgerAccountKindEnum.LOAN_REFUND, 1), Account),
        (loan_key(LedgerAccountKindEnum.EQUITY_OPENING, 1), Account),
        (category_key(1, LedgerAccountClassEnum.INCOME), Category),
        (category_key(1, LedgerAccountClassEnum.EXPENSE), Category),
        (category_key(None, LedgerAccountClassEnum.INCOME), None),
        (category_key(None, LedgerAccountClassEnum.EXPENSE), None),
        (transit_key(), None),
    ])
    def test_every_kind_a_constructor_builds(self, key, expected):
        """An account for its own line, its counters and a loan's components.

        A category for a category line; no record for a bucket (the
        Uncategorized pair and Transfers in transit).  The linked key is
        spelled by hand because :func:`linked_key` reads a loaded account's
        type; its class is the one that constructor gives a Checking.
        """
        assert chart_record_type(key) is expected

    def test_a_key_no_constructor_builds_names_no_type(self):
        """An ``orphan`` key, spelled by hand, is refused as it is labelled."""
        with pytest.raises(ValueError, match="no chart label rule"):
            chart_record_type(ChartKey(
                LedgerAccountKindEnum.ORPHAN,
                LedgerAccountClassEnum.EXPENSE,
                None,
            ))


class TestTrueUpCounterKindIsTotal:
    """``anchor_correction_counter_kind`` -- ruling R-FO's dispatch, TOTAL.

    The rule the whole step turns on: what a balance assertion's difference
    MEANS is a property of the ACCOUNT.  These cases pin both halves of it --
    the per-kind answers, and the totality that makes "a new projection kind
    silently books its return to equity" impossible.
    """

    @pytest.mark.parametrize("projection_kind,expected", [
        (AccountProjectionKind.PLAIN, LedgerAccountKindEnum.ANCHOR_EQUITY),
        (AccountProjectionKind.INTEREST, LedgerAccountKindEnum.INTEREST_INCOME),
        (AccountProjectionKind.INVESTMENT, LedgerAccountKindEnum.UNREALIZED_CHANGE),
        (AccountProjectionKind.APPRECIATING,
         LedgerAccountKindEnum.UNREALIZED_CHANGE),
    ])
    def test_a_true_up_names_what_the_difference_was(
        self, projection_kind, expected,
    ):
        """Each non-loan kind's true-up books to the counter R-FO names.

        Pinned by hand, one row per kind, because these four values ARE the
        ruling: an ``INVESTMENT`` true-up that resolved to ``ANCHOR_EQUITY``
        would tie out, pass every balance assertion, and leave ``$10,623.66`` of
        measured investment return off the income statement -- which is the
        defect this step exists to close.
        """
        assert ledger_lines.anchor_correction_counter_kind(
            projection_kind, is_opening=False,
        ) is expected

    @pytest.mark.parametrize("projection_kind", list(AccountProjectionKind))
    def test_an_opening_always_books_to_equity(self, projection_kind):
        """An OPENING books to the equity row for EVERY kind, loans included.

        An opening is capital brought onto the books, not something earned: a
        Property's $350,000.00 opening as a "change in value" would say the house
        appreciated by its whole value on the day it was recorded.  Parametrized
        over the WHOLE enum -- ``AMORTIZING`` included -- because the opening
        arm answers before the dispatch is consulted, and that is exactly the
        property worth pinning.
        """
        assert ledger_lines.anchor_correction_counter_kind(
            projection_kind, is_opening=True,
        ) is LedgerAccountKindEnum.ANCHOR_EQUITY

    def test_a_true_up_refuses_the_kind_that_books_elsewhere(self):
        """``AMORTIZING`` has no true-up rule here and says so loudly.

        A loan's corrections are the loan rules' entirely
        (:func:`app.services.ledger_lines.loan_correction_targets`), so there
        is no honest answer to give -- and returning a default would put a
        loan's balance on two equity accounts.
        """
        with pytest.raises(ValueError, match="no true-up counter rule"):
            ledger_lines.anchor_correction_counter_kind(
                AccountProjectionKind.AMORTIZING, is_opening=False,
            )

    def test_the_dispatch_covers_every_kind_the_classifier_returns(self):
        """No projection kind is left without an answer or a refusal.

        THE totality guard.  ``classify_account`` returns one of five members;
        this asserts each is either dispatched or explicitly refused, so adding
        a sixth kind to the enum fails HERE -- at a named test -- instead of
        booking that kind's return to equity in silence.  A dict lookup cannot
        be made exhaustive by the language, so it is made exhaustive by a test.
        """
        answered, refused = [], []
        for projection_kind in AccountProjectionKind:
            try:
                ledger_lines.anchor_correction_counter_kind(
                    projection_kind, is_opening=False,
                )
            except ValueError:
                refused.append(projection_kind)
            else:
                answered.append(projection_kind)

        # The loop above puts every member in exactly one list, so asserting
        # their union is the enum would be a tautology; the refusal set is the
        # guard, and a kind added without a rule lands in it.
        assert refused == [AccountProjectionKind.AMORTIZING], (
            "every non-loan projection kind must name what its true-up WAS; "
            f"unruled kinds: {refused}"
        )
