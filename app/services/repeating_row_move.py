"""
Shekel Budget App -- the date a repeating row on a card is left with by a move

**Ruling plan#2991**: a repeating bill or a standing transfer on a card keeps or takes
its date by the rule a one-time row does, ``one_off.due_date_after_move``, the one
statement of it.  This assembles what that function reads for such a row, for the three
doors that move one: the popover (``routes/transactions/_field_updates``), the transfer
door (``transfer_service._placed``) and Carry Forward (``carry_forward_service._context``).

**What a door does with the answer differs from a one-time row in one way**: it writes
the row's date and never ``occurs_on``.  A one-time row answers its own date (R-BAL25);
a repeating row answers the occurrence its rule named, so the repeat writes no second
row for it, and no occurrence index can refuse the day.

Apart from :mod:`app.services.card_today` because it needs ``one_off``, which imports
the recurrence engine, and the engine needs ``card_today``.
"""

from datetime import date

from app.models.account import Account
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.services.card_today import card_today_of_repeating_row
from app.services.one_off import RowToMove, due_date_after_move
from app.services.pay_calendar import DerivedPeriod
from app.services.recurrence import compute_due_date


def repeating_date_after_move(
    row: Transaction | Transfer,
    stated: date,
    target: DerivedPeriod,
    *,
    account: Account | None = None,
) -> date | None:
    """Return the day repeating *row* is due once moved to *target*, or ``None``.

    Args:
        row: The row, before any write.
        stated: The date the save leaves on it (:attr:`~.one_off.RowToMove.stated`).
        target: The paycheck it moves to.
        account: For a transfer, the account the save leaves it paying into
            (:func:`~app.services.card_today.card_today_of_repeating_row`).

    Returns:
        Its due date after the move; ``None`` when the move leaves its date alone
        (:func:`~app.services.card_today.card_today_of_repeating_row`).
    """
    card_today = card_today_of_repeating_row(row, account=account)
    if card_today is None:
        return None
    return due_date_after_move(
        RowToMove(
            stated=stated,
            held=row.due_date,
            source_start=row.pay_period.start_date,
            card_today=card_today,
            is_extra_payment=isinstance(row, Transfer),
            rule_day=(
                None if row.occurs_on is None
                else compute_due_date(
                    row.template.recurrence_rule, row.occurs_on, target,
                )
            ),
        ),
        target,
    )
