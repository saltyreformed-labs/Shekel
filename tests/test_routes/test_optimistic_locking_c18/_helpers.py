"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_routes/test_optimistic_locking_c18.py``, moved verbatim by ``tools/split``.
"""

from datetime import date
from decimal import Decimal

from sqlalchemy import text

from app.extensions import db
from app.models.paycheck_line import PaycheckLine
from app.models.ref import (
    AccountType,
    CalcMethod,
    PaycheckLineKind,
    FilingStatus,
    RaiseType,
    Status,
    TransactionType,
)
from app.models.salary_profile import SalaryProfile
from app.models.salary_raise import SalaryRaise
from app.models.savings_goal import SavingsGoal
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.transaction_template import TransactionTemplate
from app.models.transfer_template import TransferTemplate
from app.services import account_service
from app.utils.dates import display_today
from app.models.amount_ownership import AmountOwnership
from tests._test_helpers import (
    figure_source_columns,
    generate_row_of,
    make_expense_template,
    repriced_by_the_owner,
    start_test_pay_list,
    state_template_price,
)


# ── Helpers ─────────────────────────────────────────────────────────


def _bump_version_outside_session(schema, table, row_id):
    """Bump ``version_id`` on a table row from a fresh DB connection.

    Simulates a concurrent commit by another browser tab.  Uses a
    connection independent of the test session so the calling
    session's identity map keeps the stale value while the database
    row carries the bumped value.

    The connection commit is essential: without it the UPDATE would
    sit in an open transaction and READ COMMITTED MVCC would hide
    the bump from the test session.
    """
    with db.engine.connect() as conn:
        conn.execute(
            text(
                f"UPDATE {schema}.{table} "
                "SET version_id = version_id + 1 "
                "WHERE id = :id"
            ),
            {"id": row_id},
        )
        conn.commit()


def _read_version(model, row_id):
    """Refetch a row and return its ``version_id`` after expiring the session."""
    db.session.expire_all()
    obj = db.session.get(model, row_id)
    return obj.version_id if obj is not None else None


# ── Test data factories (kept tight so each test reads as a story) ─


def _make_template(user_id, account_id, category_id):
    """Insert a TransactionTemplate priced at $100.00 and return it with id populated.

    Its price is stated through the write door, as both create doors do, so
    its edit form renders the Amount box a real definition's does.
    """
    expense_type = (
        db.session.query(TransactionType).filter_by(name="Expense").one()
    )
    template = TransactionTemplate(
        user_id=user_id,
        account_id=account_id,
        category_id=category_id,
        transaction_type_id=expense_type.id,
        name="Optimistic-Lock Test",
        is_active=True,
    )
    db.session.add(template)
    db.session.commit()
    state_template_price(template, Decimal("100.00"))
    db.session.commit()
    return template


def _make_transaction(seed_user, period):
    """Return a row OWNING `$50.00` in *period*: the ENGINE's row, re-priced by the owner.

    C-18's subject is the ROW's optimistic lock at the PATCH door: an amount
    edit writes the row and bumps its counter, and a race on that UPDATE is
    a 409.  A ONE-OFF's typed figure restates its DEFINITION (ruling
    R-BAL29; the definition's counter and the card's pin are graded in
    ``test_one_off_row_doors``), so the row is never UPDATEd and the race
    cannot fire -- which is why this fixture is a recurring definition's row
    made the owner's (:func:`generate_row_of` + :func:`repriced_by_the_owner`,
    the re-price door's two acts) rather than the one-off builder's row
    (plan step balance:X-bi-7c, ruling R-BAL60).
    """
    template = make_expense_template(
        db.session, seed_user, amount="50.00", name="Test Txn",
        category_key="Groceries",
    )
    txn = repriced_by_the_owner(generate_row_of(template, period), "50.00")
    db.session.commit()
    return txn


def _make_envelope_template_and_txn(seed_user, period):
    """Insert a tracked (envelope) definition + its engine-generated row; return both.

    The row is the definition's own (:func:`generate_row_of`, plan step
    balance:X-cf-4); the cases below read its ``version_id`` off the row
    rather than assuming one.
    """
    template = make_expense_template(
        db.session, seed_user, amount="400.00", name="Tracked Groceries",
        category_key="Groceries", is_envelope=True,
    )
    txn = generate_row_of(template, period)
    db.session.commit()
    return template, txn


def _make_entry(txn_id, user_id):
    """Insert a TransactionEntry on the given transaction."""
    # The parent, resolved here rather than taken as an argument: this
    # helper's whole point is that a caller passes IDS.  The entry takes its
    # account (the parent's, because no door here says otherwise -- it is the
    # movement's OWN since plan step credit_card:CC-5-1) and its owner (the
    # parent's, which ``fk_transaction_entries_owner_transaction`` refuses to
    # see any other way).
    parent = db.session.get(Transaction, txn_id)
    entry = TransactionEntry(
        **figure_source_columns(),
        transaction_id=txn_id,
        account_id=parent.account_id,
        owner_id=parent.user_id,
        user_id=user_id,
        amount=Decimal("25.00"),
        description="Kroger",
        # The APP's today: the PATCH route re-posts this value and
        # ``_reject_future_purchase_date`` judges it on ``display_today()``
        # (ruling R-M), so a process date one day ahead is refused as a
        # future entry.  Same defect as the c19 concurrent-entry test.
        purchased_on=display_today(),
    )
    db.session.add(entry)
    db.session.commit()
    return entry


def _make_savings_account(user_id):
    """Insert a savings Account that goals can reference."""
    savings_type = (
        db.session.query(AccountType).filter_by(name="Savings").one()
    )
    acct = account_service.create_account(
        account_service.AccountSpec(
            user_id=user_id,
            account_type_id=savings_type.id,
            name="Optimistic-Lock Savings",
            anchor_balance=Decimal("0.00"),
        ),
    )
    db.session.add(acct)
    db.session.commit()
    return acct


def _make_savings_goal(user_id, account_id):
    """Insert a fixed-mode savings goal."""
    goal = SavingsGoal(
        user_id=user_id,
        account_id=account_id,
        name="Emergency Fund",
        target_amount=Decimal("10000.00"),
    )
    db.session.add(goal)
    db.session.commit()
    return goal


def _make_salary_profile(user_id, scenario_id):
    """Insert a SalaryProfile and return it (no template / linked txns)."""
    filing_status = (
        db.session.query(FilingStatus).filter_by(name="single").one()
    )
    profile = SalaryProfile(
        period_end_days_before_payday=6,
        user_id=user_id,
        scenario_id=scenario_id,
        filing_status_id=filing_status.id,
        name="Day Job",
        state_code="NC",
    )
    db.session.add(profile)
    start_test_pay_list(profile, Decimal("2307.69"))  # $60,000.00 a year / 26
    db.session.commit()
    return profile


def _make_salary_raise(profile_id):
    """Insert a SalaryRaise (percentage-based, recurring)."""
    raise_type = (
        db.session.query(RaiseType).filter_by(name="merit").one()
    )
    sraise = SalaryRaise(
        salary_profile_id=profile_id,
        raise_type_id=raise_type.id,
        effective_date=date(2027, 6, 1),
        percentage=Decimal("0.03"),
    )
    db.session.add(sraise)
    db.session.commit()
    return sraise


def _make_paycheck_line(profile_id):
    """Insert a PaycheckLine (fixed amount, pre-tax)."""
    timing = (
        db.session.query(PaycheckLineKind).filter_by(name="pre_tax_deduction").one()
    )
    method = (
        db.session.query(CalcMethod).filter_by(name="flat").one()
    )
    ded = PaycheckLine(
        salary_profile_id=profile_id,
        paycheck_line_kind_id=timing.id,
        calc_method_id=method.id,
        name="Health Insurance",
        amount=Decimal("100.00"),
    )
    db.session.add(ded)
    db.session.commit()
    return ded


def _make_transfer(seed_user, period):
    """Insert a transfer (and its two shadows) via the service."""
    from app.services import transfer_service  # pylint: disable=import-outside-toplevel

    savings = _make_savings_account(seed_user["user"].id)
    projected = (
        db.session.query(Status).filter_by(name="Projected").one()
    )
    cat = seed_user["categories"]["Groceries"]
    xfer = transfer_service.create_transfer(
        transfer_service.TransferSpec(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=savings.id,
            pay_period_id=period.id,
            scenario_id=seed_user["scenario"].id,
            amount_ownership=AmountOwnership.own(Decimal("250.00")),
            status_id=projected.id,
            category_id=cat.id,
            name="Test Xfer",
        ),
    )
    db.session.commit()
    return xfer


def _make_transfer_template(user_id, from_acct_id, to_acct_id, category_id):
    """Insert a TransferTemplate priced at $500.00, stated as :func:`_make_template` states its."""
    tmpl = TransferTemplate(
        user_id=user_id,
        from_account_id=from_acct_id,
        to_account_id=to_acct_id,
        category_id=category_id,
        name="Monthly Savings",
        is_active=True,
    )
    db.session.add(tmpl)
    db.session.commit()
    state_template_price(tmpl, Decimal("500.00"))
    db.session.commit()
    return tmpl
