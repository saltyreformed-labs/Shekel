"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_routes/test_templates.py``, moved verbatim by ``tools/split``.
"""

from decimal import Decimal

from app.extensions import db
from app.models.category import Category
from app.models.ref import AccountType, TransactionType
from app.models.scenario import Scenario
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.models.user import User, UserSettings
from app.services.auth_service import hash_password
from app.services import account_service
from app.services.balance_at import BalanceContext
from app.services.generation_schedule import GenerationSchedule
from tests._test_helpers import (
    all_periods,
    make_cadence_rule,
    repriced_by_the_owner,
    state_template_price,
)


# ── Helpers ──────────────────────────────────────────────────────────

#: Which ``seed_periods_today`` period contains today, stated once.
#:
#: That fixture generates 10 biweekly periods "so today falls in period 4", so
#: indices 0-3 are CLOSED.  A create form defaults its first occurrence to
#: today (plan step R7c-b), and a closing bound below that date is refused as
#: "ends before it starts" -- so a test wanting a reachable bound counts
#: FORWARD from here rather than naming a low index.
_TODAYS_PERIOD_INDEX = 4


def _create_template(seed_user, name="Rent", amount="1200.00",
                     txn_type="Expense", cadence=None):
    """Create a transaction template for the test user.

    Args:
        seed_user: The seed_user fixture dict.
        name: Template name.
        amount: Default amount string.
        txn_type: 'income' or 'expense'.
        cadence: A ``tests.oracles.recurrence_baseline`` cadence constant,
            or ``None`` for a template that does not repeat.

    Returns:
        TransactionTemplate: the created template.
    """
    txn_type_obj = db.session.query(TransactionType).filter_by(name=txn_type).one()
    category = seed_user["categories"]["Rent"]

    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=category.id,
        transaction_type_id=txn_type_obj.id,
        name=name,
    )
    db.session.add(template)
    db.session.flush()
    state_template_price(template, Decimal(amount))
    if cadence:
        # Authored through the write door, which is what plan step R7c-b made
        # the only way to make a rule: ``unit_id``, ``placement_id``,
        # ``shift_id`` and ``starts_on`` are ``NOT NULL``, so naming a pattern
        # and nothing else no longer produces a row.  ONTO the template, which
        # plan step R-F6 made the order: the rule carries its owner's FK, so
        # the definition has to exist first.
        make_cadence_rule(template, cadence)
    db.session.commit()
    return template


def _future_override_txn(seed_user, template, amount="1500.00"):
    """Generate a template's instances and override the latest (future) one.

    The latest instance is comfortably on or after today, so it sits inside
    the update route's regeneration window and a subsequent amount-change
    edit collides with it.  Returns the committed override Transaction
    (is_override=True, carrying its own ``amount``).
    """
    from app.services import recurrence_engine
    scenario = seed_user["scenario"]
    periods = all_periods(seed_user["user"].id)
    recurrence_engine.generate_for_template(template, GenerationSchedule.for_period_ids(
        BalanceContext.build(template.user_id), {p.id for p in periods},
    ), scenario.id)
    db.session.flush()
    txn = (
        db.session.query(Transaction)
        .filter_by(template_id=template.id)
        .order_by(Transaction.due_date.desc())
        .first()
    )
    repriced_by_the_owner(txn, amount)
    db.session.commit()
    return txn


def _create_other_user_with_template():
    """Create a second user with their own template.

    Returns:
        dict with keys: user, account, category, template.
    """
    other_user = User(
        email="other@shekel.local",
        password_hash=hash_password("otherpass"),
        display_name="Other User",
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

    checking_type = db.session.query(AccountType).filter_by(name="Checking").one()
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
        user_id=other_user.id, name="Baseline", is_baseline=True,
    )
    db.session.add(scenario)

    category = Category(
        user_id=other_user.id,
        group_name="Home",
        item_name="Rent",
    )
    db.session.add(category)
    db.session.flush()

    txn_type = db.session.query(TransactionType).filter_by(name="Expense").one()
    template = TransactionTemplate(
        user_id=other_user.id,
        account_id=account.id,
        category_id=category.id,
        transaction_type_id=txn_type.id,
        name="Other Rent",
    )
    db.session.add(template)
    db.session.commit()

    return {
        "user": other_user,
        "account": account,
        "category": category,
        "template": template,
    }
