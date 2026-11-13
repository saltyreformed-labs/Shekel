"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_services/test_calendar_service.py``, moved verbatim by ``tools/split``.
"""

from decimal import Decimal

from app import ref_cache
from app.enums import StatusEnum, TxnTypeEnum
from app.models.transaction_template import TransactionTemplate

from tests._test_helpers import (
    one_off_row_of,
    settle_day_columns,
    cover_bare_settled_row,
    state_template_price,
)
from tests._test_helpers import default_settle_day, make_cadence_rule
from app.services.pay_rhythm import FixedDays
from app.services.pay_calendar import PayCadence, calendar_for


#: The cadence ``seed_periods`` builds: 14 days between paydays, 26 a year.
#: An explicit input to the infrequent badge since plan step R7a-2b, where the
#: predicate was an enumerated set of pattern names that could not vary by
#: owner at all.
_BIWEEKLY = PayCadence(FixedDays(14))


#: A monthly-paid owner: 30 days between paydays, 12 a year.  The cadence that
#: makes "every 2 paychecks" a DIFFERENT answer from the biweekly one.
_MONTHLY_PAID = PayCadence(FixedDays(30))


# ── Helpers ──────────────────────────────────────────────────────────


def _income_type_id(db_session):
    """Get the Income transaction type ID from the database."""
    return ref_cache.txn_type_id(TxnTypeEnum.INCOME)


def _expense_type_id(db_session):
    """Get the Expense transaction type ID from the database."""
    return ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)


def _add_transaction(
    db_session, seed_user, period, name, amount,
    is_income=False, due_date=None,
    is_deleted=False, status=StatusEnum.PROJECTED, settled_amount=None,
):
    """Create a ONE-OFF transaction for testing.

    It took a ``template`` to link until plan step balance:X-cf-3; a row of
    a RECURRING definition is the engine's (:func:`generate_row_of`), and
    since plan step balance:X-bi-7c this builder places a one-off through
    the producer (:func:`one_off_row_of`), dated on its paycheck's start
    unless *due_date* says otherwise -- a case that means an UNDATED row
    builds a row no definition prices (a transfer shadow here; a CC payback
    is the other, and the one production holds undated), the shapes left
    undated since the family's cutover (plan step balance:X-bi-7d-2).

    Args:
        db_session: Active database session.
        seed_user: The seed_user fixture dict.
        period: PayPeriod to assign to.
        name: Transaction name.
        amount: Estimated amount (Decimal or str).
        is_income: Whether this is income (default expense).
        due_date: Optional due_date override.
        is_deleted: Soft-delete flag.
        status: StatusEnum member; defaults to PROJECTED.  Mixed-status
            calendar tests (F-3 / W-065) pass DONE, CANCELLED, CREDIT
            to assert the balance-contributing predicate filters them
            correctly.  They passed SETTLED -- the terminal archive -- until
            plan step **balance:X-am** deleted that status; the predicate
            under test reads ``excludes_from_balance`` and never a member, so
            any settled status is the same specimen.
        actual_amount: Optional realized amount.  Required for a SETTLED status
            so ``effective_amount`` returns the realized hit rather than
            falling back to ``estimated_amount``.

    Returns:
        The created Transaction.
    """
    type_id = (
        ref_cache.txn_type_id(TxnTypeEnum.INCOME)
        if is_income
        else ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
    )
    status_id = ref_cache.status_id(status)
    txn = one_off_row_of(
        period,
        name=name,
        amount=Decimal(str(amount)),
        user_id=period.user_id,
        account_id=seed_user["account"].id,
        scenario_id=seed_user["scenario"].id,
        transaction_type_id=type_id,
        due_date=due_date,
    )
    txn.status_id = status_id
    txn.is_deleted = is_deleted
    # The settle day laid on BARE, as ``add_txn`` lays it (X-f1); the
    # record is the covering movement written after the flush (X-bi-4b-2).
    for _column, _value in settle_day_columns(default_settle_day(period, status_id)).items():
        setattr(txn, _column, _value)
    db_session.flush()
    if default_settle_day(period, status_id) is not None:
        cover_bare_settled_row(db_session, txn, amount, settled_amount)
    return txn


def _make_template_with_cadence(
    db_session, seed_user, cadence, interval_n=1, *, name="Template",
):
    """Create a priced template with a recurrence rule of the given cadence.

    **The rule's first occurrence is the schedule's OPENING payday whatever
    the cadence**, so every definition built here -- annual, quarterly,
    every third paycheck -- names an occurrence in ``seed_periods[0]`` and
    :func:`generate_row_of` writes its row there (plan step balance:X-cf).
    The infrequency badge reads the CADENCE and nothing about the row's
    date, so which day the cadence anchors on is not a fact these cases
    grade; what they need is one paycheck every cadence fires in.

    Args:
        db_session: Active database session.
        seed_user: The seed_user fixture dict.
        cadence: A ``tests.oracles.recurrence_baseline`` cadence constant.
            A definition that does not repeat -- which since plan step
            R2e-3 means it names NO rule at all -- is built by a case that
            generates under a cadence and then CLEARS it, the way the edit
            door does (``_clear_recurrence_rule``), since a rule-less
            definition has rows only that way.
        interval_n: The authored interval, read only by the cadence that
            fixes none of its own.
        name: The definition's name, which its rows carry.

    Returns:
        The created TransactionTemplate.
    """
    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=list(seed_user["categories"].values())[0].id,
        transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
        name=name,
    )
    db_session.add(template)
    db_session.flush()
    state_template_price(template, Decimal("100.00"))
    # The definition first, then the cadence onto it (plan step R-F6).
    make_cadence_rule(
        template, cadence, interval_n=interval_n,
        starts_on=calendar_for(seed_user["user"].id).opening_bound(),
    )
    return template
