"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_services/test_pay_period_remove_earlier.py``, moved verbatim by ``tools/split``.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import BusinessDayShiftEnum, TxnTypeEnum
from app.exceptions import PayPeriodRemovalRefused
from app.extensions import db as _db
from app.models.journal_entry import JournalEntry
from app.models.pay_era import PayEra
from app.models.transaction_template import TransactionTemplate
from app.models.transfer_template import TransferTemplate
from app.services import pay_period_admin
from tests._test_helpers import (
    all_periods,
    make_cadence_rule,
    populate_in_a_fresh_pass,
    state_template_price,
)
from tests.oracles.recurrence_baseline import MONTHLY


NONE = BusinessDayShiftEnum.NONE


PRIOR = BusinessDayShiftEnum.PRIOR


NEXT = BusinessDayShiftEnum.NEXT


def _paydays(user_id):
    """Return the owner's recorded paydays, ascending."""
    return [period.start_date for period in all_periods(user_id)]


def _stored_eras(user_id):
    """Return the owner's era rows as ``(effective_from, cadence_days, nominal_day, other_day)``."""
    return [
        (row.effective_from, row.cadence_days, row.nominal_day, row.other_day)
        for row in PayEra.query.filter_by(user_id=user_id)
        .order_by(PayEra.effective_from)
    ]


def _unchanged(user_id, paydays, eras):
    """Assert a refused door left the owner's paydays and eras as they were.

    Read after a COMMIT and a fresh session, so a staged write a rollback
    would have hidden cannot pass as a refusal that wrote nothing.
    """
    _db.session.commit()
    _db.session.remove()
    assert _paydays(user_id) == paydays
    assert _stored_eras(user_id) == eras


def _added_head(user_id, count):
    """Add *count* paychecks before the owner's first, populated, and commit.

    Through C18-b's own door and the population a route owes it, so the head
    holds exactly what an owner's "Add earlier paychecks" leaves in it.
    """
    created = pay_period_admin.add_earlier_pay_periods(user_id, count)
    populate_in_a_fresh_pass(user_id, {period.id for period in created})
    _db.session.commit()
    return sorted(created, key=lambda period: period.start_date)


def _monthly_bill_from(seed_user, starts_on, *, is_envelope=False):
    """Author a priced monthly "Rent" on the seed account from *starts_on*.

    A CALENDAR-space rule, because only such a rule can name a day below
    the record (C18-b's route suite states why): an every-paycheck bill
    starts at the record it was authored against and never reaches a
    paycheck added below it.  The seeded books open 2024-01-04, so the
    books bound admits every added paycheck here.
    """
    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=seed_user["categories"]["Rent"].id,
        transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
        name="Rent",
        default_amount=Decimal("1200.00"),
        is_envelope=is_envelope,
    )
    _db.session.add(template)
    _db.session.flush()
    state_template_price(template)
    make_cadence_rule(template, MONTHLY, starts_on=starts_on)
    return template


def _monthly_transfer_from(seed_user, to_account, starts_on):
    """Author a priced monthly transfer, Checking to *to_account*, from *starts_on*."""
    template = TransferTemplate(
        user_id=seed_user["user"].id,
        from_account_id=seed_user["account"].id,
        to_account_id=to_account.id,
        name="To Savings",
        default_amount=Decimal("200.00"),
    )
    _db.session.add(template)
    _db.session.flush()
    state_template_price(template)
    make_cadence_rule(template, MONTHLY, starts_on=starts_on)
    return template


def _entries_in(period_ids):
    """Return the journal entries filed in *period_ids* as ``(id, source kind id)``."""
    return _db.session.query(JournalEntry.id, JournalEntry.source_kind_id).filter(
        JournalEntry.pay_period_id.in_(list(period_ids)),
    ).all()


def _held(user_id, first_kept):
    """Try the removal from *first_kept* and return the refusal's message."""
    with pytest.raises(PayPeriodRemovalRefused) as refused:
        pay_period_admin.remove_earlier_pay_periods(user_id, first_kept.id)
    return str(refused.value)
