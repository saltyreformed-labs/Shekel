"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_services/test_recurring_view.py``, moved verbatim by ``tools/split``.
"""

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import TxnTypeEnum
from app.extensions import db
from tests._test_helpers import make_cadence_rule, state_template_price
from app.models.ref import AccountType
from app.models.transaction_template import TransactionTemplate
from app.models.transfer_template import TransferTemplate
from app.services import account_service
from app.services.balance_at import BalanceContext
from app.services.pay_calendar import calendar_for


# ── Helpers ──────────────────────────────────────────────────────────


def _calendar(periods):
    """Return the owner's schedule as the surface reads it.

    ``recurring_view.build_view`` takes a
    :class:`~app.services.pay_calendar.PayCalendar` rather than a period list
    since plan step R4b-1: a recurrence's next date is measured against the
    OWNER's schedule, so the surface has to be handed that schedule rather
    than rebuild one per row.

    **Loaded through the one door since plan step C2-b2**, rather than built
    from the rows the caller happens to hold.  ``calendar_for`` reads the whole
    payday set and the owner's cadence and derives the rest, so the calendar a
    test hands the surface is the one the route would.

    Args:
        periods: The owner's pay periods -- read only for whose they are.

    Returns:
        The :class:`~app.services.pay_calendar.PayCalendar` for their owner.
    """
    return calendar_for(periods[0].user_id)


def _ctx(seed_user, as_of=None):
    """Return the read pass the surface takes (plan step R7d-d).

    ``build_view`` and ``build_archived_rows`` take a
    :class:`~app.services.balance_at.BalanceContext` rather than a calendar
    and an as-of since that step: the pass carries the owner's schedule AND
    the scenario a loan payment's stop is folded in, so the two cannot be
    handed in disagreeing.  Built the way the route builds it -- at
    ``date.today()`` -- unless a case pins the day the way its fixtures do.

    Args:
        seed_user: The owner.
        as_of: The pass's "now", or ``None`` for today.

    Returns:
        The :class:`~app.services.balance_at.BalanceContext`.
    """
    return BalanceContext.build(
        seed_user["user"].id, date.today() if as_of is None else as_of,
    )


def _author_cadence(tmpl, cadence, *, interval_n=1,
                    day_of_month=None, month_of_year=None, end_date=None):
    """Author *cadence* onto *tmpl*, through the write door.

    Through that door since plan step R7c-b, which made the two-axis columns
    NOT NULL: a rule naming only a pattern no longer produces a row.  It takes
    the OWNING template since plan step R-F6, which put the owning FK on
    ``budget.recurrence_rules`` -- so a rule cannot exist before the definition
    it belongs to, and these fixtures build the definition first.
    """
    return make_cadence_rule(
        tmpl, cadence,
        interval_n=interval_n,
        fires_on_day=day_of_month,
        fires_in_month=month_of_year,
        end_date=end_date,
    )


def _create_txn_template(seed_user, cadence, amount, *, type_enum, name,
                         **rule_kwargs):
    """Create and flush an income or expense TransactionTemplate.

    ``cadence`` may be ``None`` -- that is how a definition says "does not
    repeat" since plan step R2e-3.
    """
    tmpl = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=seed_user["categories"]["Rent"].id,
        transaction_type_id=ref_cache.txn_type_id(type_enum),
        name=name,
        is_active=True,
    )
    db.session.add(tmpl)
    db.session.flush()
    # Its price, through the one write door as the create route states it:
    # the surface prices a definition by its series since plan step X-bp.
    state_template_price(tmpl, amount)
    if cadence is not None:
        _author_cadence(tmpl, cadence, **rule_kwargs)
    return tmpl


def _create_expense(seed_user, cadence, amount, *, name="Expense",
                    **rule_kwargs):
    """Create and flush an expense TransactionTemplate with its cadence."""
    return _create_txn_template(
        seed_user, cadence, amount, type_enum=TxnTypeEnum.EXPENSE, name=name,
        **rule_kwargs,
    )


def _create_income(seed_user, cadence, amount, *, name="Income",
                   **rule_kwargs):
    """Create and flush an income TransactionTemplate with its cadence."""
    return _create_txn_template(
        seed_user, cadence, amount, type_enum=TxnTypeEnum.INCOME, name=name,
        **rule_kwargs,
    )


def _create_savings(seed_user, name="Test Savings"):
    """Create and flush a savings Account for the seed user."""
    savings_type = db.session.query(AccountType).filter_by(name="Savings").one()
    account = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=savings_type.id,
            name=name,
            anchor_balance=Decimal("5000.00"),
        ),
    )
    db.session.add(account)
    db.session.flush()
    return account


def _create_transfer(seed_user, cadence, amount, to_account, *,
                     name="Transfer", **rule_kwargs):
    """Create and flush a recurring TransferTemplate with its cadence."""
    tmpl = TransferTemplate(
        user_id=seed_user["user"].id,
        from_account_id=seed_user["account"].id,
        to_account_id=to_account.id,
        name=name,
        is_active=True,
    )
    db.session.add(tmpl)
    db.session.flush()
    state_template_price(tmpl, amount)
    _author_cadence(tmpl, cadence, **rule_kwargs)
    return tmpl
