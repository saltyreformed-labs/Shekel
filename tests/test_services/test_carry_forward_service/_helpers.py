"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_services/test_carry_forward_service.py``, moved verbatim by ``tools/split``.
"""

from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.amount_ownership import AmountOwnership
from app.models.ref import Status, TransactionType
from app.models.transaction_entry import TransactionEntry
from app.services import transfer_service
from app.services.cash_ledger import resolve_transaction_amount
from tests._test_helpers import (
    amount_basis_for,
    create_account_of_type,
    default_settle_day,
    figure_source_columns,
    make_cadence_rule,
    make_expense_template,
    one_off_row_of,
    settle_day_columns,
    cover_bare_settled_row,
)
from tests.oracles.recurrence_baseline import ANNUAL


def _create_transaction(seed_user, seed_periods, *,
                        status_name="Projected",
                        name="Test Expense", amount="100.00"):
    """Create a ONE-OFF test transaction in the first seed period.

    It took a ``template_id`` until plan step balance:X-cf-3; a row of a
    RECURRING definition is the engine's (:func:`generate_row_of`), and since
    plan step balance:X-bi-7c this builder places a one-off through the
    producer (:func:`one_off_row_of`: a rule-less definition plus its row).
    It can no longer spell a link-less row, and since the family's cutover
    (plan step balance:X-bi-7d-2) neither can the schema.  A case wanting the
    row soft-deleted sets ``is_deleted`` on the returned row, as
    :func:`one_off_row_of` says.

    Args:
        seed_user: The seed_user fixture dict.
        seed_periods: The seed_periods fixture list; the row goes in its first.
        status_name: Name of the status to look up.
        name: Transaction display name.
        amount: Estimated amount as string.

    Returns:
        The created Transaction object (flushed, not committed).
    """
    period = seed_periods[0]
    status = db.session.query(Status).filter_by(name=status_name).one()
    expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
    _settle_day = default_settle_day(period, status.id)

    txn = one_off_row_of(
        period,
        name=name,
        amount=Decimal(amount),
        user_id=period.user_id,
        account_id=seed_user["account"].id,
        scenario_id=seed_user["scenario"].id,
        transaction_type_id=expense_type.id,
        category_id=seed_user["categories"]["Groceries"].id,
    )
    txn.status_id = status.id
    # The settle day laid on BARE, as ``add_txn`` lays it (X-f1); the
    # record is the covering movement written after the flush (X-bi-4b-2).
    for _column, _value in settle_day_columns(_settle_day).items():
        setattr(txn, _column, _value)
    db.session.flush()
    if _settle_day is not None:
        cover_bare_settled_row(db.session, txn, amount)
    return txn


# ── Transfer Carry Forward Tests ───────────────────────────────────


def _create_savings(seed_user):
    """Create a savings account for transfer tests.

    Through the shared factory rather than the ``AccountType`` lookup +
    ``AccountSpec`` block it used to spell by hand, which is the same four
    lines ``create_account_of_type`` is.  The factory also opens the account's
    BOOKS before anything a fixture dates (plan step X-f3c-2b, ruling
    **R-HG**), which is what the transfers below need: they settle in the
    seeded periods, all of them earlier than the day ``create_account`` would
    otherwise open the books on.
    """
    return create_account_of_type(
        seed_user, db.session, "Savings", "CF Savings",
        anchor_balance=Decimal("0"),
    )


def _create_transfer_in_period(seed_user, seed_periods, period_index=0):
    """Create a Projected $200.00 transfer, Checking to a new Savings, in the given period."""
    savings = _create_savings(seed_user)
    projected = db.session.query(Status).filter_by(name="Projected").one()
    xfer = transfer_service.create_transfer(
        transfer_service.TransferSpec(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=savings.id,
            pay_period_id=seed_periods[period_index].id,
            scenario_id=seed_user["scenario"].id,
            amount_ownership=AmountOwnership.own(Decimal("200.00")),
            status_id=projected.id,
            category_id=seed_user["categories"]["Rent"].id,
            name="CF Transfer",
        ),
    )
    db.session.flush()
    return xfer


# ── Override-sibling Carry Forward Tests ───────────────────────────


def _create_template(seed_user, name="Recurring Bill",
                     amount="100.00", category_key="Rent"):
    """Create a priced, every-paycheck DISCRETE definition.

    Used by the override-sibling and discrete-branch tests, whose rows are
    the engine's own (:func:`generate_row_of`, plan step balance:X-cf) --
    it built a rule-less template for rows placed by hand until X-cf-3.
    Returns the persisted template.
    """
    return make_expense_template(
        db.session, seed_user, amount=amount,
        name=name, category_key=category_key,
    )


# ── Envelope Carry Forward (Phase 4) ───────────────────────────────


def _create_envelope_template(
    seed_user, *, name="Spending Money",
    default_amount="100.00", category_key="Groceries",
):
    """Create a PRICED, every-paycheck envelope definition.

    The shared builder (:func:`make_expense_template`), so the definition is
    one the engine can generate from and its rows are the engine's own
    (:func:`generate_row_of`, plan step balance:X-cf-3b).  It took a
    ``with_rule=False`` switch until that step; the "missing target
    canonical" cases then CLEARED the rule after generating the source, and
    since plan step balance:X-bi-7a that no longer reaches the rollover at
    all -- a rule-less definition's row is a one-off and moves whole
    (``Transaction.recurs`` gates the envelope branch) -- so those cases
    re-author the cadence to one that RECURS but fires in no seed paycheck
    (:func:`_fires_in_no_seed_paycheck`).
    """
    return make_expense_template(
        db.session, seed_user, amount=default_amount,
        name=name, category_key=category_key, is_envelope=True,
    )


def _fires_in_no_seed_paycheck(template):
    """Re-author *template*'s cadence to a June-15 ANNUAL rule, and flush.

    The Father's Day shape: the definition RECURS, so its rows take the
    envelope rollover, and against a schedule that runs January to May the
    engine places nothing in any seed paycheck -- so the CREATE branch of
    ``_classify_leftover_target`` is the only one reachable.  Cleared the
    way the edit door clears a rule (dis-associate; delete-orphan), then
    re-authored, the idiom ``TestACarriedForwardLeftoverRowIsDated`` uses.
    """
    template.recurrence_rule = None
    db.session.flush()
    make_cadence_rule(template, ANNUAL, fires_on_day=15, fires_in_month=6)
    db.session.refresh(template)


def _plan_of(txn):
    """Return what *txn* PLANS -- its amount as the amount model resolves it.

    A row of a definition stores no figure (plan step X-au-e), so "the
    target's estimate is untouched" is a question to the resolver, not to the
    ``estimated_amount`` column -- which reads ``None`` before AND after any
    change to the definition's price.  A row a carry-forward bumped owns its
    figure from then on and the column is that figure; the cases that assert
    a bump read it there directly, because ownership is part of what they
    assert.
    """
    return resolve_transaction_amount(txn, amount_basis_for(txn))


def _add_entry(txn, seed_user, amount, *, description="Test purchase",
               is_credit=False):
    """Attach a TransactionEntry to *txn* and flush."""
    entry = TransactionEntry(
        **figure_source_columns(),
        transaction_id=txn.id, account_id=txn.account_id, owner_id=txn.user_id,
        user_id=seed_user["user"].id,
        amount=Decimal(amount),
        description=description,
        purchased_on=date(2026, 1, 5),
        is_credit=is_credit,
    )
    db.session.add(entry)
    db.session.flush()
    return entry
