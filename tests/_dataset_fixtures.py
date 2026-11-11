"""The rich datasets: ``seed_full_user_data``, its variants, and ``seed_entry_template``.

``tests/conftest.py`` registers each fixture here by importing it by name.
"""

from decimal import Decimal

import pytest

from app.models.savings_goal import SavingsGoal
from app.models.salary_profile import SalaryProfile
from app.models.transfer_template import TransferTemplate
from app.models.ref import (
    AccountType, FilingStatus,
)
from app.services import account_service
from tests._test_helpers import (
    generate_row_of,
    make_every_period_rule,
    make_expense_template,
    open_books_before_the_first_assertion,
    start_test_pay_list,
    state_template_price,
)


def _build_full_user_data(db, seed_user, periods):
    """Build the rich-dataset payload shared by seed_full_user_data variants.

    Extracted so both ``seed_full_user_data`` (calendar-anchored) and
    ``seed_full_user_data_today`` (today-relative) can share a single
    body and only differ in which ``periods`` fixture they consume.

    Args:
        db:        SQLAlchemy db extension (the test ``db`` fixture).
        seed_user: dict from the ``seed_user`` fixture.
        periods:   List of PayPeriod objects from a periods fixture.

    Returns:
        dict merging seed_user keys plus: periods, template, transaction,
        savings_goal, recurrence_rule, savings_account,
        transfer_template, salary_profile.
    """
    user = seed_user["user"]
    account = seed_user["account"]
    scenario = seed_user["scenario"]

    # Look up reference data.
    savings_acct_type = (
        db.session.query(AccountType).filter_by(name="Savings").one()
    )
    filing_single = (
        db.session.query(FilingStatus).filter_by(name="single").one()
    )

    # a) Recurrence rule + transaction template + transaction.  The
    # definition states its price and its cadence, and the ENGINE writes its
    # row in the first period (plan step balance:X-cf): the row is derived,
    # dated and answers an occurrence, exactly as the app's own would be.
    template = make_expense_template(
        db.session, seed_user, amount="1200.00", name="Rent Payment",
    )
    rule = template.recurrence_rule
    txn = generate_row_of(template, periods[0])

    # b) Savings goal.
    goal = SavingsGoal(
        user_id=user.id,
        account_id=account.id,
        name="Emergency Fund",
        target_amount=Decimal("10000.00"),
    )
    db.session.add(goal)

    # c) Savings account + transfer template via the canonical
    # factory (E-19, Commit 3).
    savings_account = account_service.create_account(
        account_service.AccountSpec(
            user_id=user.id,
            account_type_id=savings_acct_type.id,
            name="Savings",
            anchor_balance=Decimal("500.00"),
        ),
    )
    # **Its books open the day BEFORE its origination assertion** (plan step
    # X-f3c-2b).  The factory defaults ``observed_on`` to ``display_today()``
    # and the settle door defaults a settle day to the SAME ``display_today()``
    # -- so every fixture meaning "this account existed, then money moved on
    # it" landed the movement on the very day the books open, which ruling
    # R-HG makes unrecordable (the opening equity IS that day's closing
    # balance).  The same repair production took, and it moves no figure.
    open_books_before_the_first_assertion(db.session, savings_account)

    transfer_tpl = TransferTemplate(
        user_id=user.id,
        from_account_id=account.id,
        to_account_id=savings_account.id,
        name="Monthly Savings",
    )
    db.session.add(transfer_tpl)
    db.session.flush()
    # A definition STATES its price, as every app-side create door does.  Since
    # plan step balance:X-au-f a generated TRANSFER stores no figure and is
    # priced by this series on its own due date, so a template without one
    # generates rows ``_stated_amount`` REFUSES.
    state_template_price(transfer_tpl, Decimal("200.00"))
    # ...and its CADENCE, so a test wanting a transfer of this definition
    # takes the ENGINE's (:func:`generate_transfer_of`, plan step
    # balance:X-ch) rather than hand-building one.  No transfer is generated
    # here: unlike the transaction template above, no fixture reads one.
    make_every_period_rule(db.session, transfer_tpl)

    # d) Salary profile.
    salary_profile = SalaryProfile(
        period_end_days_before_payday=6,
        user_id=user.id,
        scenario_id=scenario.id,
        filing_status_id=filing_single.id,
        name="Day Job",
        state_code="NC",
    )
    db.session.add(salary_profile)
    # Its pay list (plan step salary:X-av-3a): $75,000.00 a year at 26 paychecks, rounded to the cent, from
    # the first period's payday -- what migration 70680a4a7405 writes for the
    # yearly figure this fixture carried before.
    start_test_pay_list(
        salary_profile, Decimal("2884.62"), periods[0].start_date,
    )

    db.session.commit()

    return {
        **seed_user,
        "periods": periods,
        "template": template,
        "transaction": txn,
        "savings_goal": goal,
        "recurrence_rule": rule,
        "savings_account": savings_account,
        "transfer_template": transfer_tpl,
        "salary_profile": salary_profile,
    }


@pytest.fixture()
def seed_full_user_data(app, db, seed_user, seed_periods):
    """Create a rich dataset for User A (the primary test user).

    Includes transaction template, transaction, savings goal, savings
    account, transfer template, and salary profile. All objects have
    distinguishable names and amounts for use in isolation testing.

    Uses the calendar-anchored ``seed_periods`` fixture, so transactions
    fall in calendar 2026.  Use ``seed_full_user_data_today`` instead
    when the test exercises a route that asks which paycheck contains today.

    Returns:
        dict merging seed_user keys plus: periods, template, transaction,
        savings_goal, recurrence_rule, savings_account,
        transfer_template, salary_profile.
    """
    return _build_full_user_data(db, seed_user, seed_periods)


@pytest.fixture()
def seed_full_user_data_today(app, db, seed_user, seed_periods_today):
    """Today-relative variant of seed_full_user_data.

    Identical payload to ``seed_full_user_data`` except the periods
    are anchored so today falls in period 4.  Use when the test
    exercises a route that internally asks which paycheck contains today
    (e.g. /dashboard).

    Returns:
        dict merging seed_user keys plus: periods, template, transaction,
        savings_goal, recurrence_rule, savings_account,
        transfer_template, salary_profile.
    """
    return _build_full_user_data(db, seed_user, seed_periods_today)


@pytest.fixture()
def seed_full_second_user_data(app, db, seed_second_user, seed_second_periods):
    """Create a rich dataset for User B (the second test user).

    Mirrors seed_full_user_data but with distinguishable names and
    amounts so isolation tests can verify data separation.

    Returns:
        dict merging seed_second_user keys plus: periods, template,
        transaction, savings_goal, recurrence_rule, savings_account,
        transfer_template, salary_profile.
    """
    user = seed_second_user["user"]
    account = seed_second_user["account"]
    scenario = seed_second_user["scenario"]
    periods = seed_second_periods

    # Look up reference data.
    savings_acct_type = (
        db.session.query(AccountType).filter_by(name="Savings").one()
    )
    filing_single = (
        db.session.query(FilingStatus).filter_by(name="single").one()
    )

    # a) Recurrence rule + transaction template + transaction, the engine's
    # own row as in ``_build_full_user_data`` (plan step balance:X-cf).
    template = make_expense_template(
        db.session, seed_second_user, amount="900.00", name="Second User Rent",
    )
    rule = template.recurrence_rule
    txn = generate_row_of(template, periods[0])

    # b) Savings goal.
    goal = SavingsGoal(
        user_id=user.id,
        account_id=account.id,
        name="Vacation Fund",
        target_amount=Decimal("5000.00"),
    )
    db.session.add(goal)

    # c) Savings account + transfer template via the canonical
    # factory (E-19, Commit 3).
    savings_account = account_service.create_account(
        account_service.AccountSpec(
            user_id=user.id,
            account_type_id=savings_acct_type.id,
            name="Savings",
            anchor_balance=Decimal("300.00"),
        ),
    )
    # **Its books open the day BEFORE its origination assertion** (plan step
    # X-f3c-2b).  The factory defaults ``observed_on`` to ``display_today()``
    # and the settle door defaults a settle day to the SAME ``display_today()``
    # -- so every fixture meaning "this account existed, then money moved on
    # it" landed the movement on the very day the books open, which ruling
    # R-HG makes unrecordable (the opening equity IS that day's closing
    # balance).  The same repair production took, and it moves no figure.
    open_books_before_the_first_assertion(db.session, savings_account)

    transfer_tpl = TransferTemplate(
        user_id=user.id,
        from_account_id=account.id,
        to_account_id=savings_account.id,
        name="Bi-Weekly Savings",
    )
    db.session.add(transfer_tpl)
    db.session.flush()
    # Its price, stated as every app-side create door states it, and its
    # cadence (see the calendar-anchored twin above).
    state_template_price(transfer_tpl, Decimal("150.00"))
    make_every_period_rule(db.session, transfer_tpl)

    # d) Salary profile.
    salary_profile = SalaryProfile(
        period_end_days_before_payday=6,
        user_id=user.id,
        scenario_id=scenario.id,
        filing_status_id=filing_single.id,
        name="Second Job",
        state_code="NC",
    )
    db.session.add(salary_profile)
    # Its pay list (plan step salary:X-av-3a): $60,000.00 a year at 26 paychecks, rounded to the cent, from
    # the first period's payday -- what migration 70680a4a7405 writes for the
    # yearly figure this fixture carried before.
    start_test_pay_list(
        salary_profile, Decimal("2307.69"), periods[0].start_date,
    )

    db.session.commit()

    return {
        **seed_second_user,
        "periods": periods,
        "template": template,
        "transaction": txn,
        "savings_goal": goal,
        "recurrence_rule": rule,
        "savings_account": savings_account,
        "transfer_template": transfer_tpl,
        "salary_profile": salary_profile,
    }


# --- Entry and Companion Fixtures -----------------------------------------


@pytest.fixture()
def seed_entry_template(app, db, seed_user, seed_periods):
    """Create a template with is_envelope=True and a transaction.

    The template is an expense-type template tied to the seed_user's checking
    account with a stated price of $500.  The engine generates its single
    projected row in the first pay period (plan step balance:X-cf), so the
    row is derived and the $500 it is worth is the template's series' answer.

    Returns:
        dict with keys: template, transaction, category, recurrence_rule.
    """
    category = seed_user["categories"]["Groceries"]
    template = make_expense_template(
        db.session, seed_user, amount="500.00",
        name="Weekly Groceries", category_key="Groceries", is_envelope=True,
    )
    rule = template.recurrence_rule
    txn = generate_row_of(template, seed_periods[0])
    db.session.commit()

    return {
        "template": template,
        "transaction": txn,
        "category": category,
        "recurrence_rule": rule,
    }
