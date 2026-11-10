"""Whether an account holds a worked-out line (ruling plan#2785, task plan#2778).

:func:`app.services.ledger_lines.accounts_hold_lines` is the one question the re-type
refusal asks at both of its doors.  What these cases pin: each kind of record makes a
line and each is seen, a line that is not the account's does not count, every scenario is
asked, and an account whose one record holding money is deleted answers ``False``.
"""

from datetime import date
from decimal import Decimal

from app.extensions import db as _db
from app.models.category import Category
from app.models.scenario import Scenario
from app.services import transfer_service
from app.services.ledger_lines import accounts_hold_lines
from tests._test_helpers import (
    create_account_of_type,
    create_loan_account,
    create_settled_cash_transaction,
    create_settled_transfer,
)


def _empty_account(owner, name="Empty"):
    """Return an account of *owner*'s holding a $0.00 opening, which books nothing."""
    return create_account_of_type(
        owner, _db.session, "Savings", name, anchor_balance=Decimal("0.00"),
    )


class TestEachKindOfRecordMakesALine:
    """A correction, a movement and a loan's terms: each is a line of its account."""

    def test_an_opening_is_one(self, app, db, seed_user):
        """A non-zero opening books on the account's own line and its counter."""
        with app.app_context():
            account = create_account_of_type(
                seed_user, _db.session, "Savings", "Opened",
                anchor_balance=Decimal("500.00"),
            )
            assert accounts_hold_lines([account]) is True

    def test_a_settled_purchase_is_one(self, app, db, seed_user):
        """A $0.00 opening holds a line once a purchase settles on it."""
        with app.app_context():
            account = _empty_account(seed_user)
            assert accounts_hold_lines([account]) is False
            create_settled_cash_transaction(
                seed_user, _db.session, seed_user["bootstrap_period"],
                Decimal("70.00"), account=account,
                category=seed_user["categories"]["Groceries"],
            )
            assert accounts_hold_lines([account]) is True

    def test_a_loans_terms_are_one(self, app, db, seed_user):
        """A configured loan's opening comes from its terms."""
        with app.app_context():
            loan = create_loan_account(
                seed_user, _db.session, name="Loan",
                principal=Decimal("20000.00"), rate=Decimal("0.05000"),
            )
            assert accounts_hold_lines([loan]) is True


class TestALineMustBeTheAccountsOwn:
    """Negative controls: what stands beside an account is not its line."""

    def test_an_account_holding_nothing_beside_one_holding_lines(
        self, app, db, seed_user,
    ):
        """The seed Checking holds its opening; the account beside it holds none."""
        with app.app_context():
            account = _empty_account(seed_user)
            assert accounts_hold_lines([seed_user["account"]]) is True
            assert accounts_hold_lines([account]) is False

    def test_any_of_several_is_enough(self, app, db, seed_user):
        """The type edit asks of all a type's accounts, and none is an answer too."""
        with app.app_context():
            account = _empty_account(seed_user)
            assert accounts_hold_lines([account, seed_user["account"]]) is True
            assert accounts_hold_lines([]) is False

    def test_each_account_is_asked_in_its_own_owners_ledger(
        self, app, db, seed_user, seed_second_user,
    ):
        """Another owner's account with lines is not this owner's to answer for.

        The second owner's Checking holds its opening and the seed owner's empty
        account none, each read in its own ledger, so both together answer ``True``
        and the empty one alone ``False``.
        """
        with app.app_context():
            account = _empty_account(seed_user)
            theirs = seed_second_user["account"]
            assert accounts_hold_lines([account]) is False
            assert accounts_hold_lines([account, theirs]) is True

    def test_a_category_line_sharing_the_accounts_id_is_not_its_line(
        self, app, db, seed_user,
    ):
        """A key's record id means an account only on a key that names an account.

        The test database's id offsets keep accounts' and categories' ids apart, so
        the category is inserted with the empty account's id: the line beside it,
        on the seeded Checking under that category, carries the same number.
        """
        with app.app_context():
            account = _empty_account(seed_user)
            category = Category(
                id=account.id, user_id=seed_user["user"].id,
                group_name="Shared", item_name="Number",
            )
            _db.session.add(category)
            _db.session.flush()
            create_settled_cash_transaction(
                seed_user, _db.session, seed_user["bootstrap_period"],
                Decimal("70.00"), category=category,
            )
            assert accounts_hold_lines([account]) is False


class TestEveryScenarioIsAsked:
    """A line in any scenario of the owner's is the account's history."""

    def test_a_line_only_in_another_scenario_counts(self, app, db, seed_user):
        """A purchase settled in a what-if holds a line there, and only there."""
        with app.app_context():
            account = _empty_account(seed_user)
            whatif = Scenario(
                user_id=seed_user["user"].id, name="What-if", is_baseline=False,
            )
            _db.session.add(whatif)
            _db.session.flush()
            create_settled_cash_transaction(
                seed_user, _db.session, seed_user["bootstrap_period"],
                Decimal("70.00"), account=account, scenario=whatif,
                category=seed_user["categories"]["Groceries"],
                settled_on=date(2024, 1, 5),
            )
            assert accounts_hold_lines([account]) is True


class TestADeletedRecordLeavesNoLine:
    """The answer follows the records: a line goes with the record behind it."""

    def test_a_settled_transfer_deleted_leaves_no_line(
        self, app, seed_user,
    ):
        """A settled transfer makes a line, and its hard delete takes it away.

        The ``True`` before the delete is asserted, so the ``False`` is a change
        shown rather than a world with nothing in it.
        """
        with app.app_context():
            account = _empty_account(seed_user)
            transfer = create_settled_transfer(
                seed_user, _db.session, seed_user["account"], account,
                seed_user["bootstrap_period"], amount=Decimal("60.00"),
            )
            assert accounts_hold_lines([account]) is True
            transfer_service.delete_transfer(
                transfer.id, seed_user["user"].id, soft=False,
            )
            _db.session.flush()

            assert accounts_hold_lines([account]) is False
