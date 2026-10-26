"""The fixtures every part of this package shares: the module-level fixtures of
``tests/test_services/test_service_log_events.py``, moved verbatim by ``tools/split``.  A
fixture here reaches exactly the tests that module held, at the scope it declared.
"""

from decimal import Decimal

import pytest

from app.models.ref import Status
from app.models.transaction_template import TransactionTemplate
from app.services import account_service
from tests._test_helpers import (
    generate_row_of,
    make_every_period_rule,
    make_expense_template,
    one_off_row_of,
    open_books_before_the_first_assertion,
)


# ── Transfer service ───────────────────────────────────────────────


@pytest.fixture
def _transfer_setup(app, db, seed_user, seed_periods):
    """Build the minimum state needed for a transfer.

    seed_user has a Checking account; the transfer needs a second
    account (Savings), whose books open before the schedule (below).
    """
    from app.models.ref import AccountType  # noqa: WPS433

    savings_type = db.session.query(AccountType).filter_by(name="Savings").one()
    savings = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=savings_type.id,
            name="Savings",
            anchor_balance=Decimal("0.00"),
        ),
    )
    db.session.add(savings)
    # RE-EXPRESSED at plan step pay_calendar:C18-a under CLAUDE.md rule 5,
    # developer-confirmed 2026-09-22 ("Yes: open books before rows"): the
    # account's books open before the schedule, as the seeded account's do.
    # ``create_account`` opens them TODAY, after the paychecks these rows are
    # generated into, and since ruling R-PC85 a definition's occurrences start
    # above the books of every account it moves money in.
    open_books_before_the_first_assertion(db.session, savings)
    db.session.commit()

    projected = db.session.query(Status).filter_by(name="Projected").one()

    return {
        "user": seed_user["user"],
        "account": seed_user["account"],
        "savings": savings,
        "scenario": seed_user["scenario"],
        "periods": seed_periods,
        "projected": projected,
        "category": seed_user["categories"]["Rent"],
    }


# ── Credit workflow (legacy per-transaction) ───────────────────────


@pytest.fixture
def _projected_expense(app, db, seed_user, seed_periods):
    """Build a Projected expense in the first seeded period."""
    expense = db.session.query(
        # The TransactionType import lives in app.models.ref; lookup
        # by name is cheaper than another import.
        Status.__table__.metadata.tables["ref.transaction_types"]
    )  # noqa: pylint=unused-variable
    from app.models.ref import TransactionType  # noqa: WPS433
    expense_type = db.session.query(
        TransactionType,
    ).filter_by(name="Expense").one()

    txn = one_off_row_of(
        seed_periods[0],
        name="Test Expense",
        amount=Decimal("50.00"),
        user_id=seed_periods[0].user_id,
        account_id=seed_user["account"].id,
        scenario_id=seed_user["scenario"].id,
        transaction_type_id=expense_type.id,
        category_id=seed_user["categories"]["Groceries"].id,
    )
    db.session.flush()
    db.session.commit()
    return txn


# ── Entry service ──────────────────────────────────────────────────


@pytest.fixture
def _envelope_transaction(app, db, seed_user, seed_periods):
    """Build a Projected envelope-tracked expense for entry tests.

    The engine's own row of a priced, every-paycheck envelope definition
    (:func:`generate_row_of`, plan step balance:X-cf), in the opening
    paycheck.
    """
    template = make_expense_template(
        db.session, seed_user, amount="400.00",
        name="Groceries Envelope", category_key="Groceries", is_envelope=True,
    )
    txn = generate_row_of(template, seed_periods[0])
    db.session.commit()
    return txn


# ── Recurrence engine ──────────────────────────────────────────────


@pytest.fixture
def _recurrence_setup(app, db, seed_user, seed_periods):
    """Build a template + every-period rule to drive the recurrence engine."""
    from app.models.ref import TransactionType  # noqa: WPS433
    expense_type = db.session.query(
        TransactionType,
    ).filter_by(name="Expense").one()

    # Authored through the write door (plan step R7c-b): a rule naming only a
    # pattern is a NOT NULL violation now, and ``make_every_period_rule``
    # starts it on the schedule's OPENING payday -- ``seed_periods[0]``, the
    # period this used to name directly.
    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=seed_user["categories"]["Groceries"].id,
        transaction_type_id=expense_type.id,
        name="Recurring Test",
        default_amount=Decimal("100.00"),
        is_active=True,
    )
    db.session.add(template)
    db.session.commit()
    # The definition first, then the cadence onto it (plan step R-F6).
    rule = make_every_period_rule(db.session, template)
    return template
