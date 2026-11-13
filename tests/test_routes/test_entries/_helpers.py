"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_routes/test_entries.py``, moved verbatim by ``tools/split``.
"""

from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.category import Category
from app.models.scenario import Scenario
from app.models.transaction_entry import TransactionEntry
from app.models.ref import AccountType, TransactionType
from app.models.user import User, UserSettings
from app.services.auth_service import hash_password
from app.services import account_service

from tests._test_helpers import (
    figure_source_columns,
    generate_row_of,
    make_expense_template,
    one_off_row_of,
    record_paydays_across_a_hole,
    rhythm_of,
)


# The three days the derived reconciled indicator turns on.  FIXED rather
# than today-relative: the indicator compares two STORED days, so nothing
# about it reads a clock, and a fixture that did would be calendar-dependent
# for no reason (.claude/rules/testing.md; findings N-131 / N-132).
_PURCHASED_ON = date(2026, 1, 5)


_ASSERTED_ON = date(2026, 1, 10)


_POSTED_AFTER_THE_STATEMENT = date(2026, 1, 12)


def _add_entry(txn, user, amount, description,
               purchased_on=None, is_credit=False):
    """Create an entry directly via ORM (bypasses service validation).

    Args:
        txn: Parent Transaction object.
        user: dict with 'user' key (seed_user shape) or User object.
        amount: Decimal-compatible string or Decimal.
        description: Entry description.
        purchased_on: Date object (defaults to 2026-01-05).
        is_credit: Boolean.

    Returns:
        Committed TransactionEntry object.
    """
    uid = user["user"].id if isinstance(user, dict) else user.id
    entry = TransactionEntry(
        **figure_source_columns(),
        transaction_id=txn.id, account_id=txn.account_id, owner_id=txn.user_id,
        user_id=uid,
        amount=Decimal(str(amount)),
        description=description,
        purchased_on=purchased_on or date(2026, 1, 5),
        is_credit=is_credit,
    )
    db.session.add(entry)
    db.session.commit()
    return entry


def _create_visible_tracked_txn(seed_user, seed_periods):
    """Create a tracked, companion-visible definition and its engine-generated row.

    ``seed_entry_template`` with ``companion_visible=True`` stated at
    creation time, to avoid session-expiry issues with in-place
    modification; the row is the definition's own (:func:`generate_row_of`,
    plan step balance:X-cf-4), as that fixture's is.

    Args:
        seed_user: The seed_user fixture dict.
        seed_periods: List of PayPeriod objects.

    Returns:
        dict with keys: template, transaction, category.
    """
    category = seed_user["categories"]["Groceries"]
    template = make_expense_template(
        db.session, seed_user, amount="500.00", name="Weekly Groceries",
        category_key="Groceries", is_envelope=True, companion_visible=True,
    )
    txn = generate_row_of(template, seed_periods[0])
    db.session.commit()

    return {"template": template, "transaction": txn, "category": category}


def _create_other_user_txn():
    """Create a second owner with a pay period and a one-off of their own.

    Used for cross-user isolation tests.  The row is a rule-less
    definition's whose ``is_envelope`` is False, so it is not entry-capable.

    Returns:
        dict with keys: user, transaction.
    """
    other_user = User(
        email="other_owner@shekel.local",
        password_hash=hash_password("otherpass"),
        display_name="Other Owner",
    )
    db.session.add(other_user)
    db.session.flush()


    # The account_service factory requires the user to have at least one pay
    # period to anchor against.
    # Through the writer that owns the table (plan step pay_calendar:C4-b-1).
    from datetime import date as _date
    from tests._test_helpers import open_owner_calendar as _open_calendar
    _bootstrap = _open_calendar(other_user.id, _date(2024, 1, 5))[0]
    settings = UserSettings(user_id=other_user.id)
    db.session.add(settings)

    checking_type = db.session.query(AccountType).filter_by(
        name="Checking",
    ).one()
    account = account_service.create_account(
        account_service.AccountSpec(
            user_id=other_user.id,
            account_type_id=checking_type.id,
            name="Other Checking",
            anchor_balance=Decimal("500.00"),
        ),
    )
    db.session.add(account)

    scenario = Scenario(
        user_id=other_user.id,
        name="Baseline",
        is_baseline=True,
    )
    db.session.add(scenario)
    db.session.flush()

    category = Category(
        user_id=other_user.id,
        group_name="Family",
        item_name="Groceries",
    )
    db.session.add(category)
    db.session.flush()

    periods = record_paydays_across_a_hole(
        user_id=other_user.id,
        first_payday=date(2026, 1, 2),
        num_periods=3,
        rhythm=rhythm_of(14),
    )
    db.session.flush()

    expense_type = db.session.query(TransactionType).filter_by(
        name="Expense",
    ).one()

    txn = one_off_row_of(
        periods[0],
        name="Other Groceries",
        amount=Decimal("300.00"),
        user_id=periods[0].user_id,
        account_id=account.id,
        scenario_id=scenario.id,
        transaction_type_id=expense_type.id,
        category_id=category.id,
    )
    db.session.commit()

    return {"user": other_user, "transaction": txn}
