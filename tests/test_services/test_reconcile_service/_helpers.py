"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_services/test_reconcile_service.py``, moved verbatim by ``tools/split``.
"""

from dataclasses import replace
from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import TxnTypeEnum
from app.extensions import db
from app.models.transaction_entry import TransactionEntry
from app.models.transaction_template import TransactionTemplate
from app.services import cash_ledger, reconcile_service
from app.services.pay_calendar import calendar_for
from tests._test_helpers import (
    figure_source_columns,
    generate_row_of,
    make_cadence_rule,
    make_expense_template,
    make_income_template,
    state_template_price,
)
from tests.oracles.recurrence_baseline import MONTHLY


def _make_entry(transaction, user, amount="50.00", description="Kroger",
                purchased_on=None, is_credit=False, account_id=None):
    """Create an entry directly via ORM (bypasses service validation).

    The twin of ``test_entry_service``'s helper, carried with the tests that
    use it rather than imported across test modules: it exists to build a row
    WITHOUT the service under test, so a shared version would couple two
    modules' fixtures to one shape for no gain.  *account_id* is the account
    the purchase's money moved through (plan step ``credit_card:CC-5-2``);
    the row's own when omitted, which is every case but the card swipe.
    """
    entry = TransactionEntry(
        **figure_source_columns(),
        transaction_id=transaction.id,
        account_id=transaction.account_id if account_id is None else account_id,
        owner_id=transaction.user_id,
        user_id=user.id,
        amount=Decimal(amount),
        description=description,
        purchased_on=purchased_on or date(2026, 1, 5),
        is_credit=is_credit,
    )
    db.session.add(entry)
    db.session.flush()
    return entry


_OBSERVED_ON = date(2026, 1, 10)


def _statement(account_id, observed_on=_OBSERVED_ON):
    """Return the STATEMENT these tests reconcile against, for *account_id*.

    Plan step **X-f3a-1** (ruling **R-FL**) made every door here take the
    governing ASSERTION rather than a bare civil day, because a tick now records
    WHICH statement showed the money and production carries three days on which
    one account holds more than one assertion.

    **The id is the account's REAL assertion and the day is the test's.**  The
    id has to be real: ``fk_transactions_reconciled_by`` and
    ``fk_transaction_entries_reconciled_by`` refuse a link to a statement that
    does not exist, which is exactly what those composite keys are for.  The day
    does not, and these tests are why it is separable -- they grade the SET
    OPERATION (which rows a bound admits), so the day is an input to the filter
    rather than a fact about the seeded account, as this module's own scope note
    says.

    Args:
        account_id: The account whose governing assertion to borrow.
        observed_on: The civil day to present it for.

    Returns:
        The :class:`~app.services.cash_ledger.AnchorPoint` to reconcile against.
    """
    return replace(
        cash_ledger.governing_anchor(account_id), observed_on=observed_on,
    )


def _reconciled(seed_user, account_id=None, observed_on=_OBSERVED_ON,
                owner_id=None):
    """Return the STATEMENT these tests reconcile against.

    The value both halves of the package take since pay-calendar plan step
    **C4-a-2**: the owner's pay CALENDAR (whose rows may be offered, and the
    span each is dated against), the account, and the assertion.  Built here so
    a case that wants a MISMATCHED pair -- another owner's calendar against this
    owner's account -- says so by naming the odd one out rather than by
    assembling three values by hand.

    Args:
        seed_user: The seeded owner bundle.
        account_id: The account to reconcile; the seed user's own when omitted.
        observed_on: The civil day to present the assertion for.
        owner_id: Whose CALENDAR to date the rows against; the seed user's when
            omitted.  Named separately from *account_id* precisely so the
            ownership cases can cross the two.

    Returns:
        The :class:`~app.services.reconcile_service.Statement`.
    """
    account = seed_user["account"].id if account_id is None else account_id
    owner = seed_user["user"].id if owner_id is None else owner_id
    return reconcile_service.Statement(
        calendar_for(owner), account, _statement(account, observed_on),
    )


_BEFORE_THE_STATEMENT = date(2026, 1, 5)


_AFTER_THE_STATEMENT = date(2026, 1, 12)


def _outstanding_debit(txn, seed_user, amount="50.00",
                       purchased_on=_BEFORE_THE_STATEMENT):
    """Attach one debit purchase with NO recorded posting day."""
    return _make_entry(
        txn, seed_user["user"], amount=amount,
        description="Kroger", purchased_on=purchased_on,
    )


def _transaction_arm_bill(seed_user, period, *, name="Electricity", amount="180.00",
                          fires_on_day=None, income=False):
    """Create a projected NON-envelope row -- a bill, or a deposit.

    The engine's own row of a priced definition (:func:`generate_row_of`,
    plan step balance:X-cf).  A bill that names no day of the month
    repeats every paycheck and is dated on the paycheck's own start;
    one that names *fires_on_day* is a MONTHLY bill on that day, which
    is how a row comes to be due on the 8th or the 14th -- the engine
    dates it there (``compute_due_date``), and it lands in *period*
    because that paycheck covers the day.

    Args:
        seed_user: The seeded owner bundle.
        period: The paycheck the row is funded in.  For a monthly bill
            it must be the one covering the day it fires on.
        name: The definition's name.
        amount: Its stated price.
        fires_on_day: The day of the month a monthly bill falls due, or
            ``None`` for an every-paycheck one.
        income: Build a deposit rather than a bill.
    """
    builder = make_income_template if income else make_expense_template
    if fires_on_day is None:
        template = builder(
            db.session, seed_user, amount=amount,
            name=name, category_key="Groceries",
        )
    else:
        type_id = ref_cache.txn_type_id(
            TxnTypeEnum.INCOME if income else TxnTypeEnum.EXPENSE,
        )
        template = TransactionTemplate(
            user_id=seed_user["user"].id,
            account_id=seed_user["account"].id,
            category_id=seed_user["categories"]["Groceries"].id,
            transaction_type_id=type_id,
            name=name,
            default_amount=Decimal(amount),
            is_envelope=False,
        )
        db.session.add(template)
        db.session.flush()
        state_template_price(template)
        make_cadence_rule(template, MONTHLY, fires_on_day=fires_on_day)
    return generate_row_of(template, period)


def _transaction_arm_offered(seed_user, observed_on=_OBSERVED_ON):
    """Return ``{offer key: offer}`` for the seed user's account (a row's id, a leg's pair)."""
    return {
        group.settle.key: group.settle
        for group in reconcile_service.outstanding_set(
            _reconciled(seed_user, observed_on=observed_on),
        ).groups
        if group.settle is not None
    }


def _transaction_arm_settle(seed_user, ids, corrections=None,
                            observed_on=_OBSERVED_ON, transfer_ids=(),
                            transfer_corrections=None):
    """Run the WRITE UNION against the seed user's own account.

    It was ``record_settled_transactions`` until plan step X-f2-c3, which
    made the two source-row arms one writer parameterised by an ``Arm``
    (finding **N-225**) -- so there is no per-arm entry point left to call
    and the union is the door.  *ids* are the rows' ticks and
    *transfer_ids* the transfers' -- each arm's own form field since leaf
    ``balance:X-bi-6-4c-2`` (ruling **R-BAL145**), re-scoped by that arm.
    """
    return reconcile_service.record_reconciliation(
        reconcile_service.ReconcileSubmission(
            statement=_reconciled(seed_user, observed_on=observed_on),
            entry_ids=set(),
            transaction_ids=set(ids),
            corrections=corrections or {},
            transfer_ids=set(transfer_ids),
            transfer_corrections=transfer_corrections or {},
        ),
    )
